"""SPARQL access to the Wikidata Query Service.

The composer id list is fetched up front in one cheap query (~15s for the whole
population), and the detail query then runs over batches of those ids bound
with VALUES. Paging on the server was tried and removed: keyset paging only
works if the ORDER BY and the seek FILTER agree on an order, and on WDQS they
do not. ``ORDER BY ?item`` is not lexicographic -- Q0-Q255 are inlined as byte
IVs and lead every result set (they even survive a
``FILTER(STR(?item) > STR(wd:Q101424951))`` that should exclude them), and a
QID that is a strict prefix of another sorts *after* it (Q7294 comes back
behind Q7294821). A STR()-based cursor consequently leapt to wd:Q255 on the
first page and skipped 55% of the population, Bach and Brahms included; see
issue #181. Driving the batches from a client-side id list drops the cursor
entirely and makes coverage checkable: every id bound in a VALUES block must
come back, and ``_fetch_page`` fails the run if one does not.

Each page is followed by a second, cheap VALUES query for per-item popularity
metrics (sitelink/statement/identifier counts and the P86 backlink count). All
queries go via POST (responses bypass the WDQS edge cache, and large VALUES
blocks would exceed URL length limits as GET parameters).
"""

from __future__ import annotations

from typing import Any

import httpx
from composer_http import call_with_retries

from .parse import METRICS, _literal

SPARQL_URL = "https://query.wikidata.org/sparql"
PAGE_SIZE = 500
REQUEST_DELAY_S = 1.0
# 5 attempts back off 2+4+8+16s — long enough to ride out a laptop
# sleep/wake network blip, not just a WDQS hiccup
RETRIES = 5

# The whole population in one query. Cheap because it touches only the
# occupation index: no OPTIONAL joins, no label service, no ORDER BY.
ID_QUERY = """\
SELECT ?item WHERE { ?item wdt:P106 wd:Q36834 . }
"""

# The single-valued fields, plus the label. Each item contributes a handful of
# rows here: the truthy date/place/id properties have one best value apiece.
QUERY = """\
SELECT ?item ?itemLabel ?birth ?birthPrecision ?death ?deathPrecision
       ?birthPlaceLabel ?deathPlaceLabel ?musicbrainz
WHERE {{
  VALUES ?item {{ {values} }}
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
  OPTIONAL {{ ?item wdt:P434 ?musicbrainz . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
"""

# The multi-valued fields, one per row via UNION rather than side-by-side
# OPTIONALs. OPTIONALs multiply: a well-documented composer has hundreds of
# altLabels (every language) times a dozen genres times several countries, and
# the cross product ran to tens of thousands of rows per item -- 57MB of JSON
# that WDQS truncates mid-stream at its 60s cap. UNION adds instead of
# multiplies, so a page costs the sum of the values, not the product. Each row
# binds exactly one of the four variables; ``_fold_rows`` reads whichever is
# present, so these rows concatenate onto the QUERY rows above.
MULTI_QUERY = """\
SELECT ?item ?countryLabel ?genreLabel ?movementLabel ?alias
WHERE {{
  VALUES ?item {{ {values} }}
  {{ ?item wdt:P27 ?country . }}
  UNION {{ ?item wdt:P136 ?genre . }}
  UNION {{ ?item wdt:P135 ?movement . }}
  UNION {{ ?item skos:altLabel ?alias . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
"""

# Per-item popularity metrics: Wikidata precomputes sitelink/statement/
# identifier counts as single-valued triples, and the P86 backlink count
# (works naming the item as composer) proxies how much the composer is
# played/recorded. Joining these into the paged QUERY above times out the
# query planner, so they are fetched per page via their own VALUES query.
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


def _qid(row: dict[str, Any]) -> str:
    """The QID from a row's ?item binding."""
    item: str = row["item"]["value"]
    return item.rsplit("/", 1)[-1]


def _values(qids: list[str]) -> str:
    """QIDs as the body of a SPARQL VALUES block."""
    return " ".join(f"wd:{qid}" for qid in qids)


def _fetch_qids(client: httpx.Client) -> list[str]:
    """Every composer QID, numerically ordered."""
    qids = {_qid(row) for row in _run_query(client, ID_QUERY, "composer id list")}
    # numeric, not lexicographic: QIDs carry no leading zeros, so (length,
    # string) is numeric order. It keeps the low -- and therefore best known --
    # QIDs in the first batches, which is what a max_pages smoke run sees.
    return sorted(qids, key=lambda qid: (len(qid), qid))


def _fetch_page(client: httpx.Client, qids: list[str]) -> list[dict[str, Any]]:
    """Fetch the detail rows for one batch of composers: the single-valued
    fields, then the multi-valued ones (see MULTI_QUERY for why they are a
    separate query). The two row lists concatenate -- ``_fold_rows`` groups by
    QID and reads whichever variables a row binds -- and QUERY's rows must come
    first, since the label is taken from the first row seen for an item.

    In QUERY every pattern besides the VALUES block is OPTIONAL, so each bound
    item must produce at least one row; a short answer means rows went missing
    in transit rather than an item having no properties. Raising here is what
    stops a silent hole from quietly halving the dataset (issue #181)."""
    rows = _run_query(client, QUERY.format(values=_values(qids)), f"page of {len(qids)} items")
    missing = set(qids) - {_qid(row) for row in rows}
    if missing:
        raise RuntimeError(
            f"wikidata page returned {len(qids) - len(missing)} of {len(qids)} requested items; "
            f"missing e.g. {sorted(missing)[:5]}"
        )
    # no coverage check here: an item with none of these properties, and so no
    # row at all, is ordinary
    return rows + _run_query(client, MULTI_QUERY.format(values=_values(qids)), f"multi for {len(qids)} items")


def _fetch_metrics(client: httpx.Client, qids: list[str]) -> dict[str, dict[str, str]]:
    """Popularity metrics keyed by QID for the given items."""
    query = METRICS_QUERY.format(values=_values(qids))
    metrics: dict[str, dict[str, str]] = {}
    for row in _run_query(client, query, f"metrics for {len(qids)} items"):
        metrics[_qid(row)] = {var: value for var, _ in METRICS if (value := _literal(row, var)) is not None}
    return metrics
