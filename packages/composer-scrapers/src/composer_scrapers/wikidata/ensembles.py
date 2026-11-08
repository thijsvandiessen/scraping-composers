"""Orchestras, choirs, string quartets and opera companies, from the Wikidata
Query Service.

The population is every item that is an instance of (a subclass of) one of
``ENSEMBLE_CLASSES``, plus the plain "musical group"/"musical ensemble" items
with a classical signal (``MUSICAL_GROUPS``): many major orchestras, the
London and Royal Philharmonic or the Budapest Symphony, are typed only that
way (#363). It is paged exactly like the composer population (see the
``query`` module): cheap id-list queries, then VALUES batches, each batch's
single-valued answer checked for coverage.

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
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from composer_http import SourceSession
from composer_schema import CLASSICAL_NAME_CUES, ensemble_type_from_name

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence, SourceClaim
from .parse import _BARE_QID, BASE_URL, _literal
from .query import LABEL_LANGUAGES, PAGE_SIZE, REQUEST_DELAY_S, TIMEOUT_S, _qid, _run_query, _values

log = logging.getLogger(__name__)

#: Wikidata class -> ensemble type. The population is every instance of a
#: subclass of one of these (live, 2026-10: about 5,800 orchestras, 4,100
#: choirs, 500 string quartets, 300 opera companies and a few dozen chamber
#: ensembles). Deliberately not "musical group" (Q215380) as a class: its
#: subclass tree is every rock band on Wikidata, 115,000 items none of the
#: sources credit; see ``MUSICAL_GROUPS``.
ENSEMBLE_CLASSES: dict[str, str] = {
    "Q42998": "orchestra",
    "Q131186": "choir",
    "Q207338": "chamber_ensemble",  # string quartet
    "Q9390516": "chamber_ensemble",  # chamber ensemble (instrumentalists and singers)
    "Q19191604": "chamber_ensemble",  # chamber music ensemble (instrumentalists only)
    "Q20819922": "opera_company",
}

#: "musical group" and "musical ensemble". A *direct* instance of one is in the
#: population only with a classical signal: a genre (P136) under
#: ``CLASSICAL_GENRES``, or a name cue (``NAME_CUES``) and no genre outside
#: them. Direct only, so the subclasses (rock band, boy band, ...) stay out.
#: These classes give no type; the names do.
MUSICAL_GROUPS = ("Q215380", "Q2088357")

#: Classical music, opera and choral music: a musical group whose genre is one
#: of these or a subclass of one is a classical ensemble. Early music and
#: contemporary classical music sit under classical music already; film score
#: does not, and its tree is anime and image songs (live, 2026-10).
CLASSICAL_GENRES = ("Q9730", "Q1344", "Q1076513")

#: "Art music": a genre that does not make a name-cued group a non-classical
#: one. The item itself only, not its subclasses: those are gamelan, qawwali
#: and free jazz.
NEUTRAL_GENRE = "Q1583807"

#: Words in a label or alias, any language, that mark a musical group with no
#: other genre as a classical ensemble: composer_schema's list, so every word
#: that admits a group also types it.
NAME_CUES = tuple(CLASSICAL_NAME_CUES)

#: Which type wins when an item is an instance of several classes. Chamber
#: ensemble first: of the four items (live, 2026-10) also filed as an
#: orchestra, two are string quartets and one a new-music ensemble, and no
#: choir reaches a chamber class. A choir also filed as an orchestra is a choir.
TYPE_PRIORITY = ("chamber_ensemble", "choir", "orchestra", "opera_company")

#: The fewest ids each id query may answer: about half its live count
#: (2026-10). Wikidata only grows these populations, so a shorter answer lost
#: a slice on the way (#181), and anchor-ensembles would unlink every
#: ensemble in it. The name query's floor counts its raw search hits: the
#: search service answers a failed search with an empty 200.
MIN_IDS = {"class": 5_000, "genre": 250, "name": 1_000}

#: How often the name query is asked when a search comes back short of its own
#: hit count: the search API's continuation is not deterministic, and a run
#: now and then drops an item across a page boundary.
NAME_QUERY_ATTEMPTS = 4

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

# Musical groups with a classical genre, walked the same way round: the genre
# subclasses first, then the items filed under them, then the class test on
# each bound item. About 580 items in two to eight seconds.
GENRE_GROUP_ID_QUERY = f"""\
SELECT DISTINCT ?item WHERE {{
  hint:Query hint:optimizer "None" .
  VALUES ?genreRoot {{ {_values(list(CLASSICAL_GENRES))} }}
  VALUES ?group {{ {_values(list(MUSICAL_GROUPS))} }}
  ?genre wdt:P279* ?genreRoot .
  ?item wdt:P136 ?genre .
  ?item wdt:P31 ?group .
}}
"""

# Musical groups with a name cue. Scanning the labels of all 115,000 musical
# groups (a REGEX on rdfs:label) runs past the 60 s limit, so this asks the
# search index instead: one ``haswbstatement:P31=<group> inlabel:<cue>`` search
# per pair, which matches labels and aliases in every language. About 2,200
# hits in 15-35 seconds. Each row carries its search and that search's own hit
# count, so a search cut short is caught (``_name_group_qids``). A group that
# binds ``?otherGenre``, a genre outside ``CLASSICAL_GENRES`` (art music
# aside), is a rock, jazz or folk band, whatever it is called, and is dropped.
# (``BIND(NOT EXISTS {...})`` on the search's ``?item`` came back true for
# every row live, so the test is an OPTIONAL read in Python.)
NAME_GROUP_ID_QUERY = f"""\
SELECT ?search ?total ?item ?otherGenre WHERE {{
  VALUES ?group {{ {" ".join(f'"{group}"' for group in MUSICAL_GROUPS)} }}
  VALUES ?cue {{ {" ".join(f'"{cue}"' for cue in NAME_CUES)} }}
  BIND(CONCAT("haswbstatement:P31=", ?group, " inlabel:", ?cue) AS ?search)
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:endpoint "www.wikidata.org" ;
                    wikibase:api "Search" ;
                    mwapi:srsearch ?search ;
                    mwapi:srlimit "max" .
    ?title wikibase:apiOutput mwapi:title .
    ?total wikibase:apiOutput "/api/query/searchinfo/@totalhits" .
  }}
  BIND(IRI(CONCAT(STR(wd:), ?title)) AS ?item)
  OPTIONAL {{
    ?item wdt:P136 ?otherGenre .
    FILTER(?otherGenre != wd:{NEUTRAL_GENRE})
    FILTER NOT EXISTS {{
      VALUES ?genreRoot {{ {_values(list(CLASSICAL_GENRES))} }}
      ?otherGenre wdt:P279* ?genreRoot .
    }}
  }}
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
# Blazegraph with a 500 (#359). A label brings its language too, for the
# display name of an item with no en/mul label.
ENSEMBLE_MULTI_QUERY = """\
SELECT ?item ?root ?name ?nameLang ?partOf ?partOfLabel
WHERE {{
  VALUES ?item {{ {values} }}
  {{ VALUES ?root {{ {classes} }} ?item wdt:P31/wdt:P279* ?root . }}
  UNION {{
    ?item rdfs:label ?name .
    FILTER(LANG(?name) IN ({name_languages}))
    BIND(LANG(?name) AS ?nameLang)
  }}
  UNION {{ ?item skos:altLabel ?name . FILTER(LANG(?name) IN ({name_languages})) }}
  UNION {{ ?item wdt:P361 ?partOf . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""


def _floored(qids: set[str], slice_: str, desc: str) -> set[str]:
    """*qids*, unless they are fewer than ``MIN_IDS[slice_]``: then the run fails."""
    if len(qids) < MIN_IDS[slice_]:
        raise RuntimeError(
            f"wikidata {desc} returned {len(qids)} ids, fewer than the {MIN_IDS[slice_]} expected; "
            "refusing a population with a slice missing"
        )
    return qids


def _short_searches(rows: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """Search -> (titles returned, hits reported), for each search that
    returned fewer titles than it reported hits."""
    titles: dict[str, set[str]] = {}
    totals: dict[str, int] = {}
    for row in rows:
        search: str = row["search"]["value"]
        titles.setdefault(search, set()).add(_qid(row))
        totals[search] = int(row["total"]["value"])
    return {s: (len(titles[s]), totals[s]) for s in titles if len(titles[s]) < totals[s]}


def _name_group_qids(session: SourceSession) -> set[str]:
    """The admitted ids of NAME_GROUP_ID_QUERY, from an answer in which every
    search returned all the hits it reported. Asked again while one comes back
    short; a failed run if it still does."""
    desc = "classical-name musical group ids"
    short: dict[str, tuple[int, int]] = {}
    for attempt in range(1, NAME_QUERY_ATTEMPTS + 1):
        rows = _run_query(session, NAME_GROUP_ID_QUERY, desc)
        short = _short_searches(rows)
        if not short:
            hits = _floored({_qid(row) for row in rows}, "name", desc)
            return hits - {_qid(row) for row in rows if "otherGenre" in row}
        log.warning(
            "%s: attempt %d had %d short searches, e.g. %s", desc, attempt, len(short), min(short.items())
        )
    raise RuntimeError(
        f"wikidata {desc}: {len(short)} searches stayed short of their hits, e.g. {min(short.items())}"
    )


def _fetch_ensemble_qids(session: SourceSession) -> list[str]:
    """Every ensemble QID, numerically ordered: the population classes'
    instances and the musical groups with a classical genre or name. Each id
    query is checked on its own, so one that lost its answer cannot hide in
    the union."""
    desc = "ensemble id list"
    qids = _floored({_qid(row) for row in _run_query(session, ENSEMBLE_ID_QUERY, desc)}, "class", desc)
    desc = "classical-genre musical group ids"
    qids |= _floored({_qid(row) for row in _run_query(session, GENRE_GROUP_ID_QUERY, desc)}, "genre", desc)
    qids |= _name_group_qids(session)
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
    """Group rows by QID: label, class roots, names, labels by language, home
    cities, part-of targets and MusicBrainz ids."""
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = items.setdefault(
            _qid(row),
            {
                "label": _literal(row, "itemLabel") or "",
                "classes": set(),
                "names": set(),
                "labels": {},
                "cities": set(),
                "part_of": set(),
                "musicbrainz": set(),
            },
        )
        if (root := _item_qid(row, "root")) is not None:
            item["classes"].add(root)
        if (name := _literal(row, "name")) is not None:
            item["names"].add(name)
            if (lang := _literal(row, "nameLang")) is not None:
                item["labels"][lang] = name
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


def ensemble_type(classes: set[str], label: str, names: Iterable[str] = ()) -> str:
    """The type the item's classes give it, else its label's, else the most
    specific one its other names give (the cue is as often in an alias or a
    label in another language), else ``other``."""
    types = {ENSEMBLE_CLASSES[c] for c in classes if c in ENSEMBLE_CLASSES}
    for candidate in TYPE_PRIORITY:
        if candidate in types:
            return candidate
    if (from_label := ensemble_type_from_name(label)) is not None:
        return from_label
    from_names = {ensemble_type_from_name(name) for name in names}
    return next((t for t in (*TYPE_PRIORITY, "band") if t in from_names), "other")


def _display_label(item: dict[str, Any]) -> str | None:
    """The en/mul label the label service gave, else the label in the first of
    ``NAME_LANGUAGES`` the item has one in (a third of the items have names
    only in German, French, Czech, ..., #363), else None."""
    label: str = item["label"]
    if label and not _BARE_QID.match(label):
        return label
    labels: dict[str, str] = item["labels"]
    return next((labels[lang] for lang in NAME_LANGUAGES if lang in labels), None)


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
        label = _display_label(item)
        if label is None:
            log.debug("skipping %s: no label", qid)
            continue
        kind_of = ensemble_type(item["classes"], label, item["names"])
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
        """Yield every orchestra, choir, string quartet and opera company on
        Wikidata, and the musical groups with a classical genre or name. ``max_pages`` caps the
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
