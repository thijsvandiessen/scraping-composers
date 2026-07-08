"""SPARQL access to the Wikidata Query Service.

Pagination uses an inner subquery over just the item ids (ORDER BY ?item with a
keyset FILTER(?item > wd:<last>) LIMIT) so each page covers a fixed set of
composers; the OPTIONAL property joins then expand each composer into one row
per property-value combination, and all rows for a composer land in the same
page. Keyset (seek) paging rather than LIMIT/OFFSET: deep OFFSET makes WDQS sort
and discard every prior row on every page and 504s past ~30k in, whereas the
FILTER range scan keeps per-page cost flat regardless of depth.

Each page is followed by a second, cheap VALUES query for per-item popularity
metrics (sitelink/statement/identifier counts and the P86 backlink count). All
queries go via POST (responses bypass the WDQS edge cache, and large VALUES
blocks would exceed URL length limits as GET parameters).
"""

from __future__ import annotations

from typing import Any

import httpx

from .._http import call_with_retries
from .parse import METRICS, _literal

SPARQL_URL = "https://query.wikidata.org/sparql"
PAGE_SIZE = 500
REQUEST_DELAY_S = 1.0
# 5 attempts back off 2+4+8+16s — long enough to ride out a laptop
# sleep/wake network blip, not just a WDQS hiccup
RETRIES = 5

QUERY = """\
SELECT ?item ?itemLabel ?birth ?birthPrecision ?death ?deathPrecision
       ?birthPlaceLabel ?deathPlaceLabel ?countryLabel ?genreLabel ?movementLabel
WHERE {{
  {{ SELECT ?item WHERE {{ ?item wdt:P106 wd:Q36834 . {after_filter} }} ORDER BY ?item LIMIT {page_size} }}
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


def _run_query(client: httpx.Client, query: str, desc: str) -> list[dict[str, Any]]:
    """Execute a SPARQL query with retries. Queries go via POST: responses to
    POST bypass the WDQS edge cache (which can serve a body truncated
    mid-stream as a cached 200 for 300s, defeating retries), and large VALUES
    blocks would exceed URL length limits as GET parameters."""

    def do() -> list[dict[str, Any]]:
        resp = client.post(SPARQL_URL, data={"query": query, "format": "json"})
        resp.raise_for_status()
        bindings: list[dict[str, Any]] = resp.json()["results"]["bindings"]
        return bindings

    # WDQS rate-limits with 429 + Retry-After; the helper honors it
    return call_with_retries(
        do, label=desc, retries=RETRIES, retry_on=(httpx.HTTPError, ValueError, KeyError)
    )


def _fetch_page(client: httpx.Client, after: str | None) -> list[dict[str, Any]]:
    """Fetch one page of composers whose QID sorts strictly after ``after`` (or
    from the start when None). Keyset paging: the inner ORDER BY ?item plus
    FILTER(?item > wd:<after>) is an indexed range scan, so page cost stays flat
    with depth where LIMIT/OFFSET does not (see module docstring)."""
    # STR(?item): SPARQL's relational operators are undefined for IRIs (a bare
    # ?item > wd:X errors, and FILTER drops every erroring row -> an empty page
    # that looks like the end). Comparing the IRI strings is well-defined and,
    # since ORDER BY ?item also sorts by IRI string, stays consistent with it.
    after_filter = f"FILTER(STR(?item) > STR(wd:{after}))" if after else ""
    query = QUERY.format(page_size=PAGE_SIZE, after_filter=after_filter)
    return _run_query(client, query, f"page after={after or 'START'}")


def _fetch_metrics(client: httpx.Client, qids: list[str]) -> dict[str, dict[str, str]]:
    """Popularity metrics keyed by QID for the given items."""
    query = METRICS_QUERY.format(values=" ".join(f"wd:{qid}" for qid in qids))
    metrics: dict[str, dict[str, str]] = {}
    for row in _run_query(client, query, f"metrics for {len(qids)} items"):
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        metrics[qid] = {var: value for var, _ in METRICS if (value := _literal(row, var)) is not None}
    return metrics
