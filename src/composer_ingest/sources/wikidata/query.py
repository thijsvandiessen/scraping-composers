"""SPARQL access to the Wikidata Query Service.

Pagination uses an inner subquery over just the item ids (ORDER BY ?item
LIMIT/OFFSET) so each page covers a fixed set of composers; the OPTIONAL
property joins then expand each composer into one row per property-value
combination, and all rows for a composer land in the same page.

Each page is followed by a second, cheap VALUES query for per-item popularity
metrics (sitelink/statement/identifier counts and the P86 backlink count). All
queries go via POST (responses bypass the WDQS edge cache, and large VALUES
blocks would exceed URL length limits as GET parameters).
"""

from __future__ import annotations

from typing import Any

import httpx

from ...http import Http
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


def _run_query(client: httpx.Client, query: str, desc: str) -> list[dict[str, Any]]:
    """Execute a SPARQL query with retries (WDQS rate-limits with 429 +
    Retry-After, which the shared Http honors). Queries go via POST: responses
    to POST bypass the WDQS edge cache (which can serve a body truncated
    mid-stream as a cached 200 for 300s, defeating retries), and large VALUES
    blocks would exceed URL length limits as GET parameters."""
    bindings: list[dict[str, Any]] = Http(client, retries=RETRIES, honor_retry_after=True).post_json(
        SPARQL_URL,
        data={"query": query, "format": "json"},
        extract=lambda payload: payload["results"]["bindings"],
        desc=desc,
    )
    return bindings


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
