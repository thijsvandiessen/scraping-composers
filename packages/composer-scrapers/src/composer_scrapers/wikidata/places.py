"""The places composers were born, died and held citizenship in, from the
Wikidata Query Service.

The ``wikidata`` composer records name these places by label and pin each claim
to its QID; this adapter fetches the places themselves, so a place entity
carries more than its name:

* ``place_type`` -- one of :data:`PLACE_TYPES`, read off the item's class
  (P31, through any depth of P279). Settlements and municipalities read as
  ``settlement``, which is what a birthplace means even where Wikidata files a
  town as an Italian *comune* or French *commune*;
* ``located_in`` (P131) and ``in_country`` (P17) -- the containing places,
  pinned by QID. The parents are fetched too, round after round, until the
  hierarchy closes, so every ``located_in`` names a place with its own record;
* ``coordinates`` (P625, as ``lat,lon``), ``geonames_id`` (P1566) and
  ``iso_3166_code`` (P297 country / P300 subdivision code) as literals.

Each record's URL is the place's Wikidata page, so ingest pins the record to
the same entity the composer claims created (label and QID both match: the
label service is asked for the same languages). Paging and mirroring work as for
the composer population (see the ``query`` module).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from composer_http import SourceSession

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence, SourceClaim
from .parse import _BARE_QID, BASE_URL, _literal
from .query import LABEL_LANGUAGES, PAGE_SIZE, REQUEST_DELAY_S, TIMEOUT_S, _qid, _run_query, _values

log = logging.getLogger(__name__)

# Every place a composer claim points at. Cheap, like the composer id list: the
# occupation index and three properties, no label service.
PLACE_ID_QUERY = """\
SELECT DISTINCT ?item WHERE {
  ?composer wdt:P106 wd:Q36834 .
  { ?composer wdt:P19 ?item . } UNION { ?composer wdt:P20 ?item . } UNION { ?composer wdt:P27 ?item . }
}
"""

# Every pattern is OPTIONAL, so each bound item returns at least its label row
# and the page can be checked for coverage. The OPTIONALs multiply, but a place
# has few coordinates, parents and countries.
PLACE_QUERY = """\
SELECT ?item ?itemLabel ?coord ?geonames ?iso ?parent ?parentLabel ?country ?countryLabel
WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P625 ?coord . }}
  OPTIONAL {{ ?item wdt:P1566 ?geonames . }}
  OPTIONAL {{ ?item wdt:P297|wdt:P300 ?iso . }}
  OPTIONAL {{ ?item wdt:P131 ?parent . }}
  OPTIONAL {{ ?item wdt:P17 ?country . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""

# (place type, Wikidata class), in precedence order: an item in several classes
# takes the first. A settlement comes first because the cities that were once
# states are filed as both (Milan, Venice, Hamburg), and a birthplace means the
# city; it also beats region because a municipality is an administrative entity
# too (Vienna is a city and a state of Austria). A former state comes before
# country because it is often both (the Soviet Union).
PLACE_CLASSES: tuple[tuple[str, str], ...] = (
    ("settlement", "Q486972"),  # human settlement
    ("settlement", "Q15284"),  # municipality
    ("historical_state", "Q3024240"),  # historical country
    ("country", "Q6256"),
    ("building", "Q41176"),
    ("region", "Q56061"),  # administrative territorial entity
)
PLACE_TYPES: tuple[str, ...] = tuple(dict.fromkeys(place_type for place_type, _ in PLACE_CLASSES))
_TYPE_OF_CLASS: dict[str, str] = {cls: place_type for place_type, cls in PLACE_CLASSES}
_PRECEDENCE: dict[str, int] = {place_type: rank for rank, place_type in enumerate(PLACE_TYPES)}

# A row per (item, class it falls under). Kept out of PLACE_QUERY: the
# subclass walk is the expensive part, and joining it would multiply rows.
PLACE_TYPE_QUERY = """\
SELECT ?item ?class
WHERE {{
  VALUES ?item {{ {values} }}
  VALUES ?class {{ {classes} }}
  ?item wdt:P31/wdt:P279* ?class .
}}
"""

#: Rounds of parent fetching before the hierarchy is cut off. A village is
#: rarely more than six levels below its country.
MAX_ROUNDS = 10

# A WKT point on Earth: "Point(16.3725 48.2083)", longitude first. Points on
# other globes carry a globe IRI prefix and do not match.
_QID = re.compile(r"Q\d+")

_POINT = re.compile(r"Point\((-?[\d.]+) (-?[\d.]+)\)")


def _fetch_place_qids(session: SourceSession) -> list[str]:
    """Every place a composer claim points at, numerically ordered. An "unknown
    value" birthplace binds a blank node, not an item, and is left out."""
    qids = {_qid(row) for row in _run_query(session, PLACE_ID_QUERY, "place id list")}
    qids = {qid for qid in qids if _QID.fullmatch(qid)}
    return sorted(qids, key=lambda qid: (len(qid), qid))


def _fetch_place_page(session: SourceSession, qids: list[str]) -> list[dict[str, Any]]:
    """The detail rows, then the class rows, for one batch of places. Every
    bound item returns a detail row, so a short answer fails the run and is not
    mirrored (issue #181)."""
    query = PLACE_QUERY.format(values=_values(qids), languages=LABEL_LANGUAGES)

    def covered(rows: list[dict[str, Any]]) -> bool:
        return not set(qids) - {_qid(row) for row in rows}

    rows = _run_query(session, query, f"page of {len(qids)} places", mirror=True, complete=covered)
    missing = set(qids) - {_qid(row) for row in rows}
    if missing:
        raise RuntimeError(
            f"wikidata page returned {len(qids) - len(missing)} of {len(qids)} requested places; "
            f"missing e.g. {sorted(missing)[:5]}"
        )
    types = PLACE_TYPE_QUERY.format(
        values=_values(qids), classes=" ".join(f"wd:{cls}" for _, cls in PLACE_CLASSES)
    )
    return rows + _run_query(session, types, f"types of {len(qids)} places", mirror=True)


def _coordinates(value: str) -> str | None:
    """A WKT point as ``lat,lon``, or None for one that is not on Earth."""
    match = _POINT.fullmatch(value)
    return f"{match.group(2)},{match.group(1)}" if match else None


def _linked(row: dict[str, Any], var: str) -> tuple[str, str] | None:
    """The (QID, label) a row binds for item variable ``var``, or None when the
    label service cannot name it."""
    item, label = _literal(row, var), _literal(row, f"{var}Label")
    if item is None or label is None or _BARE_QID.match(label):
        return None
    return item.rsplit("/", 1)[-1], label


def _fold_place_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group rows by place QID: label, place types, coordinates, GeoNames and
    ISO codes, and the parent and country items (QID -> label)."""
    places: dict[str, dict[str, Any]] = {}
    for row in rows:
        place = places.setdefault(
            _qid(row),
            {
                "label": "",
                "types": set(),
                "coordinates": set(),
                "geonames": set(),
                "iso": set(),
                "parents": {},
                "countries": {},
            },
        )
        place["label"] = place["label"] or _literal(row, "itemLabel") or ""
        if (cls := _literal(row, "class")) is not None and (
            kind := _TYPE_OF_CLASS.get(cls.rsplit("/", 1)[-1])
        ):
            place["types"].add(kind)
        if (coord := _literal(row, "coord")) is not None and (point := _coordinates(coord)) is not None:
            place["coordinates"].add(point)
        for var, key in (("geonames", "geonames"), ("iso", "iso")):
            if (value := _literal(row, var)) is not None:
                place[key].add(value)
        for var, key in (("parent", "parents"), ("country", "countries")):
            if (linked := _linked(row, var)) is not None:
                place[key][linked[0]] = linked[1]
    return places


def _place_type(types: set[str]) -> str | None:
    """The type that wins under :data:`PLACE_CLASSES` precedence, if any."""
    return min(types, key=_PRECEDENCE.__getitem__) if types else None


def _place_document(qid: str, place: dict[str, Any], ingested_at: datetime) -> EntityDocument:
    claims: list[SourceClaim] = []
    place_type = _place_type(place["types"])
    if place_type is not None:
        claims.append(SourceClaim("place_type", value=place_type))
    # a place listing itself as its own country (Germany, P17 Germany) is not
    # a containment edge
    for predicate, key in (("located_in", "parents"), ("in_country", "countries")):
        for other, label in sorted(place[key].items()):
            if other != qid:
                claims.append(SourceClaim(predicate, "place", label, object_wikidata_id=other))
    for predicate, key in (
        ("coordinates", "coordinates"),
        ("geonames_id", "geonames"),
        ("iso_3166_code", "iso"),
    ):
        claims.extend(SourceClaim(predicate, value=value) for value in sorted(place[key]))
    return EntityDocument(
        id=qid,
        url=f"{BASE_URL}/wiki/{qid}",
        source_name=WikidataPlacesAdapter.name,
        ingested_at=ingested_at,
        name=place["label"],
        kind="place",
        raw={
            "item": f"http://www.wikidata.org/entity/{qid}",
            "label": place["label"],
            "place_type": place_type,
            "types": sorted(place["types"]),
            "coordinates": sorted(place["coordinates"]),
            "geonames": sorted(place["geonames"]),
            "iso": sorted(place["iso"]),
            "parents": dict(sorted(place["parents"].items())),
            "countries": dict(sorted(place["countries"].items())),
        },
        claims=tuple(claims),
    )


class WikidataPlacesAdapter(HttpSourceAdapter[EntityDocument]):
    name = "wikidata_places"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S
    timeout_s = TIMEOUT_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every place a composer points at, then the places containing
        those, until no new parent turns up (or :data:`MAX_ROUNDS` is spent).
        ``max_pages`` caps the number of batches over all rounds, for test runs."""
        pending = _fetch_place_qids(session)
        if not pending:
            raise RuntimeError("wikidata returned no composer places")
        log.info("wikidata_places: %d places composers point at", len(pending))
        seen = set(pending)
        pages = 0
        for round_no in range(1, MAX_ROUNDS + 1):
            parents: set[str] = set()
            for start in range(0, len(pending), PAGE_SIZE):
                if max_pages is not None and pages >= max_pages:
                    log.info("stopping after max_pages=%d", max_pages)
                    return
                batch = pending[start : start + PAGE_SIZE]
                pages += 1
                ingested_at = datetime.now(UTC)
                places = _fold_place_rows(_fetch_place_page(session, batch))
                named = 0
                for qid, place in places.items():
                    if not place["label"] or _BARE_QID.match(place["label"]):
                        log.debug("skipping %s: no label", qid)
                        continue
                    named += 1
                    parents.update(place["parents"], place["countries"])
                    yield _place_document(qid, place, ingested_at)
                log.info("wikidata_places round %d, page of %d: %d places", round_no, len(batch), named)
            pending = sorted(parents - seen, key=lambda qid: (len(qid), qid))
            if not pending:
                return
            seen.update(pending)
        log.warning(
            "wikidata_places: hierarchy cut off after %d rounds, %d parents unfetched",
            MAX_ROUNDS,
            len(pending),
        )
