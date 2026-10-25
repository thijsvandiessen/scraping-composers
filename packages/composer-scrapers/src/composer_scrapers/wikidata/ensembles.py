"""Orchestras and choirs, from the Wikidata Query Service.

The population is every item that is an instance of (a subclass of) one of
``ENSEMBLE_CLASSES``. It is paged exactly like the composer population (see
the ``query`` module): one cheap id-list query, then VALUES batches, each
batch's single-valued answer checked for coverage.

What an ensemble record is for is *anchoring*: the warehouse keys an ensemble
with a QID on that QID alone, and folds the name-keyed ensembles every concert
and recording source mints onto it by the names collected here — the item's
labels and aliases in the languages the sources publish in, so "Wiener
Philharmoniker" and "Vienna Philharmonic Orchestra" both reach one item. Each
record also carries a type (``ensemble_type``), a home city (``based_in``) and
the groups it belongs to (``part_of``).

The home city is a ``place`` claim pinned to the city's QID, the way composers'
birth places are (#352), so an orchestra's Vienna is the entity the
``wikidata_places`` record and every composer born there land on.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from composer_http import SourceSession
from composer_schema import ensemble_type_from_name

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence, SourceClaim
from .parse import _BARE_QID, BASE_URL, _literal
from .query import LABEL_LANGUAGES, PAGE_SIZE, REQUEST_DELAY_S, TIMEOUT_S, _qid, _run_query, _values

log = logging.getLogger(__name__)

#: Wikidata class -> ensemble type. The population is every instance of a
#: subclass of one of these. Deliberately not "musical group" (Q215380): that
#: is every rock band on Wikidata, hundreds of thousands of items none of the
#: sources credit. Chamber ensembles and opera companies are typed from their
#: names until their classes are pinned against live Wikidata.
ENSEMBLE_CLASSES: dict[str, str] = {
    "Q42998": "orchestra",
    "Q131186": "choir",
}

#: Which type wins when an item is an instance of several classes: a choir that
#: is also filed as an orchestra is a choir.
TYPE_PRIORITY = ("chamber_ensemble", "choir", "orchestra", "opera_company")

#: Wikidata's "human settlement": a headquarters that is one is the home city
#: itself; anything else (a concert hall, a district) is read through its P131.
SETTLEMENT_CLASS = "Q486972"

#: Languages whose labels and aliases count as names of the ensemble — the
#: languages the concert and recording sources print names in. Wider than
#: LABEL_LANGUAGES on purpose: that picks the one display name, while these
#: are every name a credit might use.
NAME_LANGUAGES = ("en", "mul", "de", "fr", "it", "nl", "es", "cs", "sv", "da", "pl", "hu")

_CLASS_VALUES = _values(list(ENSEMBLE_CLASSES))

# Cheap like the composer id query: no labels, no OPTIONALs, no ORDER BY. The
# subclasses are walked first and their instances read after, in that order:
# ``?item wdt:P31/wdt:P279* ?class`` with ``?item`` unbound makes WDQS start
# from every instance of anything and times out (504) at 60 s, and its
# optimizer reorders the two patterns back into that plan unless switched off
# (#359). This way it answers in about a second.
ENSEMBLE_ID_QUERY = f"""\
SELECT DISTINCT ?item WHERE {{
  hint:Query hint:optimizer "None" .
  VALUES ?class {{ {_CLASS_VALUES} }}
  ?sub wdt:P279* ?class .
  ?item wdt:P31 ?sub .
}}
"""

# The single-valued fields. Every pattern besides VALUES is OPTIONAL, so each
# bound item comes back at least once and the page is checked for coverage.
# P159 is the headquarters; when it is not itself a settlement its P131 is.
ENSEMBLE_QUERY = """\
SELECT ?item ?itemLabel ?hq ?hqLabel ?hqSettlement ?hqAdmin ?hqAdminLabel ?musicbrainz
WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{
    ?item wdt:P159 ?hq .
    OPTIONAL {{ ?hq wdt:P131 ?hqAdmin . }}
    BIND(EXISTS {{ ?hq wdt:P31/wdt:P279* wd:{settlement} . }} AS ?hqSettlement)
  }}
  OPTIONAL {{ ?item wdt:P434 ?musicbrainz . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""

# The multi-valued fields, one per row via UNION (see MULTI_QUERY in ``query``
# for why not OPTIONALs): the population classes the item falls under, its
# names, and the groups it is part of. Labels and aliases take a branch each:
# an ``rdfs:label|skos:altLabel`` alternation inside a UNION branch crashes
# Blazegraph with a 500 (#359).
ENSEMBLE_MULTI_QUERY = """\
SELECT ?item ?root ?name ?partOf ?partOfLabel
WHERE {{
  VALUES ?item {{ {values} }}
  {{ VALUES ?root {{ {classes} }} ?item wdt:P31/wdt:P279* ?root . }}
  UNION {{ ?item rdfs:label ?name . FILTER(LANG(?name) IN ({name_languages})) }}
  UNION {{ ?item skos:altLabel ?name . FILTER(LANG(?name) IN ({name_languages})) }}
  UNION {{ ?item wdt:P361 ?partOf . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""


def _fetch_ensemble_qids(session: SourceSession) -> list[str]:
    """Every ensemble QID, numerically ordered."""
    qids = {_qid(row) for row in _run_query(session, ENSEMBLE_ID_QUERY, "ensemble id list")}
    return sorted(qids, key=lambda qid: (len(qid), qid))


def _fetch_ensemble_page(session: SourceSession, qids: list[str]) -> list[dict[str, Any]]:
    """The rows for one batch: single-valued first (the label is read from the
    first row of an item), then multi-valued. A short single-valued answer
    fails the run and is never mirrored (issue #181)."""
    query = ENSEMBLE_QUERY.format(
        values=_values(qids), settlement=SETTLEMENT_CLASS, languages=LABEL_LANGUAGES
    )

    def covered(rows: list[dict[str, Any]]) -> bool:
        return not set(qids) - {_qid(row) for row in rows}

    rows = _run_query(session, query, f"page of {len(qids)} ensembles", mirror=True, complete=covered)
    missing = set(qids) - {_qid(row) for row in rows}
    if missing:
        raise RuntimeError(
            f"wikidata page returned {len(qids) - len(missing)} of {len(qids)} requested ensembles; "
            f"missing e.g. {sorted(missing)[:5]}"
        )
    multi = ENSEMBLE_MULTI_QUERY.format(
        values=_values(qids),
        classes=_CLASS_VALUES,
        name_languages=", ".join(f'"{lang}"' for lang in NAME_LANGUAGES),
        languages=LABEL_LANGUAGES,
    )
    return rows + _run_query(session, multi, f"multi for {len(qids)} ensembles", mirror=True)


def _item_qid(row: dict[str, Any], var: str) -> str | None:
    value = _literal(row, var)
    return value.rsplit("/", 1)[-1] if value is not None else None


def _named(row: dict[str, Any], var: str) -> tuple[str, str] | None:
    """(QID, label) of the item bound to ``var``, or None when the label service
    could not name it."""
    qid, label = _item_qid(row, var), _literal(row, f"{var}Label")
    if qid is None or label is None or _BARE_QID.match(label):
        return None
    return qid, label


def _fold_ensemble_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group rows by QID: label, class roots, names, home cities, part-of
    targets and MusicBrainz ids."""
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = items.setdefault(
            _qid(row),
            {
                "label": _literal(row, "itemLabel") or "",
                "classes": set(),
                "names": set(),
                "cities": set(),
                "part_of": set(),
                "musicbrainz": set(),
            },
        )
        if (root := _item_qid(row, "root")) is not None:
            item["classes"].add(root)
        if (name := _literal(row, "name")) is not None:
            item["names"].add(name)
        if (part_of := _named(row, "partOf")) is not None:
            item["part_of"].add(part_of)
        if (musicbrainz := _literal(row, "musicbrainz")) is not None:
            item["musicbrainz"].add(musicbrainz)
        hq = _named(row, "hq")
        if hq is not None and _literal(row, "hqSettlement") == "true":
            item["cities"].add(hq)
        elif (admin := _named(row, "hqAdmin")) is not None:
            item["cities"].add(admin)
    return items


def ensemble_type(classes: set[str], label: str) -> str:
    """The type the item's classes give it, else its name's, else ``other``."""
    types = {ENSEMBLE_CLASSES[c] for c in classes if c in ENSEMBLE_CLASSES}
    for candidate in TYPE_PRIORITY:
        if candidate in types:
            return candidate
    return ensemble_type_from_name(label) or "other"


def _distinct_names(label: str, names: set[str]) -> list[str]:
    """The item's names other than its label, minus case-only variants."""
    seen = {label.casefold()}
    distinct: list[str] = []
    for name in sorted(names):
        if name.casefold() not in seen and not _BARE_QID.match(name):
            seen.add(name.casefold())
            distinct.append(name)
    return distinct


def _documents_from_rows(
    rows: list[dict[str, Any]], population: set[str], ingested_at: datetime
) -> list[EntityDocument]:
    """One ensemble document per nameable item. ``population`` is the full id
    list: a ``part_of`` target outside it is not an ensemble entity (an
    orchestra is part of a broadcaster or an opera house) and only stays in
    ``raw``."""
    documents: list[EntityDocument] = []
    for qid, item in _fold_ensemble_rows(rows).items():
        label: str = item["label"]
        if not label or _BARE_QID.match(label):
            log.debug("skipping %s: no label", qid)
            continue
        kind_of = ensemble_type(item["classes"], label)
        claims = [SourceClaim("ensemble_type", value=kind_of)]
        # several headquarters are rare; the lowest QID is the stable pick
        city = min(item["cities"], default=None, key=lambda c: (len(c[0]), c[0]))
        if city is not None:
            claims.append(SourceClaim("based_in", "place", city[1], object_wikidata_id=city[0]))
        names = _distinct_names(label, item["names"])
        claims.extend(SourceClaim("also_known_as", value=name) for name in names)
        claims.extend(SourceClaim("musicbrainz_id", value=mbid) for mbid in sorted(item["musicbrainz"]))
        part_of = sorted(item["part_of"])
        claims.extend(
            SourceClaim("part_of", "ensemble", other_label, object_wikidata_id=other)
            for other, other_label in part_of
            if other in population
        )
        documents.append(
            EntityDocument(
                id=qid,
                url=f"{BASE_URL}/wiki/{qid}",
                source_name=WikidataEnsemblesAdapter.name,
                ingested_at=ingested_at,
                name=label,
                kind="ensemble",
                raw={
                    "item": f"http://www.wikidata.org/entity/{qid}",
                    "label": label,
                    "labels": names,
                    "classes": sorted(item["classes"]),
                    "ensemble_type": kind_of,
                    "based_in": {"item": city[0], "label": city[1]} if city is not None else None,
                    "part_of": [{"item": other, "label": other_label} for other, other_label in part_of],
                    "musicbrainz": sorted(item["musicbrainz"]),
                },
                claims=tuple(claims),
            )
        )
    return documents


class WikidataEnsemblesAdapter(HttpSourceAdapter[EntityDocument]):
    name = "wikidata_ensembles"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S
    timeout_s = TIMEOUT_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every orchestra and choir on Wikidata. ``max_pages`` caps the
        number of batches, for test runs."""
        qids = _fetch_ensemble_qids(session)
        if not qids:
            raise RuntimeError("wikidata returned no ensemble ids")
        population = set(qids)
        batches = [qids[i : i + PAGE_SIZE] for i in range(0, len(qids), PAGE_SIZE)]
        if max_pages is not None and max_pages < len(batches):
            log.info("stopping after max_pages=%d", max_pages)
            batches = batches[:max_pages]
        log.info("wikidata_ensembles: %d ensembles in %d pages", len(qids), len(batches))

        for page, batch in enumerate(batches, start=1):
            documents = _documents_from_rows(
                _fetch_ensemble_page(session, batch), population, datetime.now(UTC)
            )
            log.info("wikidata_ensembles page %d/%d: %d ensembles", page, len(batches), len(documents))
            yield from documents
