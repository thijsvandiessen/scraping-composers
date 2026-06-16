"""Wikidata SPARQL client.

Fetches every item with occupation "composer" (Q36834) from the Wikidata
Query Service. Pagination uses an inner subquery over just the item ids
(ORDER BY ?item LIMIT/OFFSET) so each page covers a fixed set of composers;
the OPTIONAL property joins then expand each composer into one row per
property-value combination, and all rows for a composer land in the same
page. ``_records_from_rows`` folds those rows back into one SourceRecord per
composer.

Each page is followed by a second, cheap VALUES query for per-item
popularity metrics (sitelink/statement/identifier counts and the number of
works naming the item as composer, P86); these become literal-valued claims
like ``sitelink_count``.

Birth/death dates are stored at the precision Wikidata records for them (see
``_format_time``): a year-only fact lands as ``1756``, not the padded
``1756-01-01``, so the stored string never overstates how precisely the date
is known.

Items (or claim objects) without an English label come back from the label
service as their bare QID ("Q12345"); those are skipped since a QID is not a
name we can deduplicate on.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from typing import Any

import httpx

from . import SourceClaim, SourceRecord

NAME = "wikidata"
BASE_URL = "https://www.wikidata.org"

SPARQL_URL = "https://query.wikidata.org/sparql"
PAGE_SIZE = 500
REQUEST_DELAY_S = 1.0
# 5 attempts back off 2+4+8+16s — long enough to ride out a laptop
# sleep/wake network blip, not just a WDQS hiccup
RETRIES = 5

log = logging.getLogger(__name__)

QUERY = """\
SELECT ?item ?itemLabel ?birth ?birthPrecision ?death ?deathPrecision
       ?birthPlaceLabel ?deathPlaceLabel ?countryLabel ?genreLabel ?movementLabel
WHERE {{
  {{ SELECT ?item WHERE {{ ?item wdt:P106 wd:Q36834 . }} ORDER BY ?item LIMIT {page_size} OFFSET {offset} }}
  # Take the truthy (best-rank) date via wdt:, then join the statement value
  # node carrying that same value to read its precision. Wikidata pads unknown
  # components to 01, so without timePrecision a year-only birth is
  # indistinguishable from 1 January. Matching the value node by ?birth keeps
  # truthy semantics -- p:/psv: alone also returns deprecated/lower-rank
  # statements (Beethoven has three P569 values) -- and an "unknown value" date
  # has no timeValue to match, so it drops out instead of leaking a blank node.
  OPTIONAL {{ ?item wdt:P569 ?birth .
              ?item p:P569/psv:P569
                [ wikibase:timeValue ?birth ; wikibase:timePrecision ?birthPrecision ] . }}
  OPTIONAL {{ ?item wdt:P570 ?death .
              ?item p:P570/psv:P570
                [ wikibase:timeValue ?death ; wikibase:timePrecision ?deathPrecision ] . }}
  OPTIONAL {{ ?item wdt:P19 ?birthPlace . }}
  OPTIONAL {{ ?item wdt:P20 ?deathPlace . }}
  OPTIONAL {{ ?item wdt:P27 ?country . }}
  OPTIONAL {{ ?item wdt:P136 ?genre . }}
  OPTIONAL {{ ?item wdt:P135 ?movement . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
"""

# Per-item popularity metrics: Wikidata precomputes sitelink/statement/
# identifier counts as single-valued triples, and the P86 backlink count
# (works naming the item as composer) proxies how much the composer is
# played/recorded. Joining these into the paged QUERY above times out the
# query planner at deep offsets, so they are fetched per page via VALUES.
METRICS_QUERY = """\
SELECT ?item ?sitelinks ?statements ?identifiers (COUNT(?work) AS ?works)
WHERE {{
  VALUES ?item {{ {values} }}
  ?item wikibase:sitelinks ?sitelinks ;
        wikibase:statements ?statements ;
        wikibase:identifiers ?identifiers .
  OPTIONAL {{ ?work wdt:P86 ?item . }}
}}
GROUP BY ?item ?sitelinks ?statements ?identifiers
"""

# SPARQL result variable -> (claim predicate, object entity kind).
# kind None means the claim object is a literal (a date string).
FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("birth", "born_on", None),
    ("death", "died_on", None),
    ("birthPlaceLabel", "born_in", "place"),
    ("deathPlaceLabel", "died_in", "place"),
    ("countryLabel", "citizen_of", "place"),
    ("genreLabel", "has_genre", "genre"),
    ("movementLabel", "in_movement", "movement"),
)

# METRICS_QUERY result variable -> claim predicate (all literal-valued).
METRICS: tuple[tuple[str, str], ...] = (
    ("sitelinks", "sitelink_count"),
    ("statements", "statement_count"),
    ("identifiers", "identifier_count"),
    ("works", "work_count"),
)

_BARE_QID = re.compile(r"^Q\d+$")

# A Wikidata time literal: optional BCE sign, year (>=1 digit), zero-padded
# month and day. The time component is split off before this matches.
_TIME = re.compile(r"(-?)(\d+)-(\d{2})-(\d{2})")


def _format_time(value: str, precision: str | None) -> str:
    """Truncate a Wikidata time literal to the granularity its ``precision``
    warrants: day (11) keeps YYYY-MM-DD, month (10) keeps YYYY-MM, year (9) or
    coarser keeps just the year. Wikidata pads unknown components to 01, so
    without this a year-only date masquerades as 1 January. Precision below
    year (decade/century/...) is still rendered as the year and remains a
    coarse approximation; modeling those as ranges is left to downstream.

    Non-time values (e.g. an "unknown value" node that slips through) and a
    missing/garbled precision are passed through / treated as full-precision
    so this never raises."""
    date = value.split("T", 1)[0]  # "1756-01-27T00:00:00Z" -> "1756-01-27"
    match = _TIME.fullmatch(date)
    if match is None:
        return date
    sign, year, month, day = match.groups()
    try:
        prec = int(precision) if precision is not None else 11
    except ValueError:
        prec = 11
    if prec >= 11:
        body = f"{year}-{month}-{day}"
    elif prec == 10:
        body = f"{year}-{month}"
    else:
        body = year
    return sign + body


def _run_query(client: httpx.Client, query: str, desc: str) -> list[dict[str, Any]]:
    """Execute a SPARQL query with retries. Queries go via POST: responses to
    POST bypass the WDQS edge cache (which can serve a body truncated
    mid-stream as a cached 200 for 300s, defeating retries), and large VALUES
    blocks would exceed URL length limits as GET parameters."""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.post(SPARQL_URL, data={"query": query, "format": "json"})
            resp.raise_for_status()
            bindings: list[dict[str, Any]] = resp.json()["results"]["bindings"]
            return bindings
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if attempt == RETRIES:
                raise
            wait = 2**attempt
            # WDQS rate-limits with 429 + Retry-After; honor it when present
            if isinstance(exc, httpx.HTTPStatusError):
                retry_after = exc.response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = max(wait, int(retry_after))
            log.warning("%s failed (%s), retrying in %ds", desc, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _fetch_page(client: httpx.Client, offset: int) -> list[dict[str, Any]]:
    query = QUERY.format(page_size=PAGE_SIZE, offset=offset)
    return _run_query(client, query, f"page offset={offset}")


def _fetch_metrics(client: httpx.Client, qids: list[str]) -> dict[str, dict[str, str]]:
    """Popularity metrics keyed by QID for the given items."""
    query = METRICS_QUERY.format(values=" ".join(f"wd:{qid}" for qid in qids))
    metrics: dict[str, dict[str, str]] = {}
    for row in _run_query(client, query, f"metrics for {len(qids)} items"):
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        metrics[qid] = {var: value for var, _ in METRICS if (value := _literal(row, var)) is not None}
    return metrics


def _literal(row: dict[str, Any], var: str) -> str | None:
    """The row's value for ``var``, or None if absent or not a plain literal
    (Wikidata's "unknown value" comes back as a blank node)."""
    binding = row.get(var)
    if not binding or binding.get("type") not in ("literal", "uri"):
        return None
    value: str = binding["value"]
    return value


def _records_from_rows(
    rows: list[dict[str, Any]], metrics: dict[str, dict[str, str]] | None = None
) -> list[SourceRecord]:
    """Fold SPARQL result rows (several per composer when properties have
    multiple values) into one SourceRecord per composer, attaching the
    composer's popularity ``metrics`` (from ``_fetch_metrics``) as claims."""
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        uri = row["item"]["value"]
        qid = uri.rsplit("/", 1)[-1]
        item = items.setdefault(qid, {"label": _literal(row, "itemLabel") or "", "values": {}})
        for var, _, kind in FIELDS:
            value = _literal(row, var)
            if value is None:
                continue
            if kind is None:
                value = _format_time(value, _literal(row, f"{var}Precision"))
            elif _BARE_QID.match(value):
                continue  # claim object has no English label
            item["values"].setdefault(var, set()).add(value)

    records = []
    for qid, item in items.items():
        name = item["label"]
        if not name or _BARE_QID.match(name):
            log.debug("skipping %s: no English label", qid)
            continue
        # every record matched the occupation=composer query
        claims = [SourceClaim("has_profession", "profession", "composer")]
        for var, predicate, kind in FIELDS:
            for value in sorted(item["values"].get(var, ())):
                if kind is None:
                    claims.append(SourceClaim(predicate, value=value))
                else:
                    claims.append(SourceClaim(predicate, kind, value))
        item_metrics = (metrics or {}).get(qid, {})
        for var, predicate in METRICS:
            if var in item_metrics:
                claims.append(SourceClaim(predicate, value=item_metrics[var]))
        raw = {"item": f"http://www.wikidata.org/entity/{qid}", "label": name}
        raw.update({var: sorted(values) for var, values in item["values"].items()})
        raw.update(item_metrics)
        records.append(
            SourceRecord(
                external_id=qid,
                name=name,
                url=f"{BASE_URL}/wiki/{qid}",
                raw=raw,
                claims=tuple(claims),
            )
        )
    return records


def fetch_records(max_pages: int | None = None) -> Iterator[SourceRecord]:
    """Yield every composer on Wikidata, paging until the query is exhausted."""
    offset = 0
    pages = 0
    with httpx.Client(
        headers={"User-Agent": "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"},
        timeout=90,  # WDQS may take up to its 60s execution limit
    ) as client:
        while True:
            rows = _fetch_page(client, offset)
            page_qids = sorted({row["item"]["value"].rsplit("/", 1)[-1] for row in rows})
            metrics = _fetch_metrics(client, page_qids) if page_qids else {}
            records = _records_from_rows(rows, metrics)
            pages += 1
            log.info("wikidata page %d: %d composers (offset=%d)", pages, len(records), offset)
            yield from records

            # rows aggregate to one record per item in the subquery page, so
            # fewer than PAGE_SIZE items (incl. skipped ones) means last page
            if len(page_qids) < PAGE_SIZE:
                break
            if max_pages is not None and pages >= max_pages:
                log.info("stopping after max_pages=%d", max_pages)
                break
            offset += PAGE_SIZE
            time.sleep(REQUEST_DELAY_S)
