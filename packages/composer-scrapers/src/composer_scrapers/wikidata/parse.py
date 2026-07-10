"""Folding SPARQL result rows into one SourceRecord per composer.

Birth/death dates are stored at the precision Wikidata records for them (see
``_format_time``): a year-only fact lands as ``1756``, not the padded
``1756-01-01``, so the stored string never overstates how precisely the date is
known. Items (or claim objects) without an English label come back as their
bare QID ("Q12345") and are skipped, since a QID is not a name to dedup on.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .. import SourceClaim, SourceRecord

BASE_URL = "https://www.wikidata.org"

log = logging.getLogger(__name__)

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
    ("alias", "also_known_as", None),
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
