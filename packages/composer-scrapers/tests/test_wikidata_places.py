"""Tests for the Wikidata places adapter: the places composers point at, with
type, hierarchy, coordinates and identifiers."""

import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from composer_http.testing import mock_session
from composer_schema import EntityDocument, SourceClaim
from composer_scrapers import REGISTRY
from composer_scrapers.wikidata import WikidataPlacesAdapter
from composer_scrapers.wikidata.places import (
    PLACE_CLASSES,
    PLACE_ID_QUERY,
    PLACE_QUERY,
    PLACE_TYPE_QUERY,
    _coordinates,
    _fetch_place_page,
    _fetch_place_qids,
    _fold_place_rows,
    _place_type,
)


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}


def row(qid: str, label: str | None = None, **vars: str) -> dict[str, Any]:
    """A PLACE_QUERY or PLACE_TYPE_QUERY row. ``parent``/``country``/``class``
    values are "QID" or "QID=Label"; the rest are literals."""
    bindings: dict[str, Any] = {"item": uri(qid)}
    if label is not None:
        bindings["itemLabel"] = {"type": "literal", "value": label}
    for var, value in vars.items():
        if var in ("parent", "country", "class"):
            other, _, other_label = value.partition("=")
            bindings[var] = uri(other)
            if other_label:
                bindings[f"{var}Label"] = {"type": "literal", "value": other_label}
        else:
            bindings[var] = {"type": "literal", "value": value}
    return bindings


VIENNA = [
    row(
        "Q1741",
        "Vienna",
        coord="Point(16.3725 48.2083)",
        geonames="2761369",
        iso="AT-9",
        parent="Q40=Austria",
        country="Q40=Austria",
    ),
    row("Q1741", **{"class": "Q486972"}),
    row("Q1741", **{"class": "Q56061"}),
]


def documents(rows: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    """Fetch through the adapter with WDQS answering ``rows`` to every query
    except the id list, which names Vienna."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        if query == PLACE_ID_QUERY:
            return httpx.Response(200, json={"results": {"bindings": [{"item": uri("Q1741")}]}})
        bound = set(query.split("VALUES ?item {", 1)[1].split("}", 1)[0].split())
        answer = [r for r in rows if "wd:" + r["item"]["value"].rsplit("/", 1)[-1] in bound]
        if "?class" in query:
            answer = [r for r in answer if "class" in r]
        else:
            answer = [r for r in answer if "class" not in r]
        return httpx.Response(200, json={"results": {"bindings": answer}})

    return handler


def fetch(
    rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, max_pages: int | None = None
) -> dict[str, EntityDocument]:
    monkeypatch.setattr(WikidataPlacesAdapter, "open_session", lambda self: mock_session(documents(rows)))
    return {doc.id: doc for doc in WikidataPlacesAdapter().fetch(max_pages=max_pages)}


def test_a_place_becomes_a_record_with_type_hierarchy_and_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    austria = [row("Q40", "Austria", iso="AT", country="Q40=Austria"), row("Q40", **{"class": "Q6256"})]
    docs = fetch(VIENNA + austria, monkeypatch)

    vienna = docs["Q1741"]
    assert vienna.kind == "place"
    assert vienna.name == "Vienna"
    assert vienna.url == "https://www.wikidata.org/wiki/Q1741"  # ingest pins the entity by this
    assert vienna.source_name == "wikidata_places"
    assert vienna.claims == (
        SourceClaim("place_type", value="settlement"),  # a city before a state of Austria
        SourceClaim("located_in", "place", "Austria", object_wikidata_id="Q40"),
        SourceClaim("in_country", "place", "Austria", object_wikidata_id="Q40"),
        SourceClaim("coordinates", value="48.2083,16.3725"),  # lat,lon
        SourceClaim("geonames_id", value="2761369"),
        SourceClaim("iso_3166_code", value="AT-9"),
    )
    # the parent was fetched in a second round, and does not contain itself
    assert docs["Q40"].claims == (
        SourceClaim("place_type", value="country"),
        SourceClaim("iso_3166_code", value="AT"),
    )


def test_the_hierarchy_stops_once_no_new_parent_turns_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # Austria names Vienna back: already fetched, so no third round
    austria = [row("Q40", "Austria", parent="Q1741=Vienna")]
    assert set(fetch(VIENNA + austria, monkeypatch)) == {"Q1741", "Q40"}


def test_max_pages_counts_parent_rounds_too(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(fetch(VIENNA + [row("Q40", "Austria")], monkeypatch, max_pages=1)) == {"Q1741"}


def test_places_the_label_service_cannot_name_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = fetch([row("Q1741", "Q1741", parent="Q40=Austria")], monkeypatch)
    assert docs == {}


def test_unnamed_parents_are_not_claimed() -> None:
    place = _fold_place_rows([row("Q1741", "Vienna", parent="Q40"), row("Q1741", "Vienna", parent="Q41=Q41")])
    assert place["Q1741"]["parents"] == {}


def test_an_unknown_value_parent_is_neither_claimed_nor_fetched() -> None:
    genid = "http://www.wikidata.org/.well-known/genid/fe048b58a583dff76d1ac48db02c39be"
    unknown = row("Q1741", "Vienna")
    unknown["parent"] = {"type": "uri", "value": genid}
    unknown["parentLabel"] = {"type": "literal", "value": genid}
    assert _fold_place_rows([unknown])["Q1741"]["parents"] == {}


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        ({"historical_state", "country", "region"}, "historical_state"),  # the Soviet Union
        ({"historical_state", "settlement", "region"}, "settlement"),  # Milan, once a duchy
        ({"country", "settlement"}, "settlement"),  # a city-state
        ({"settlement", "region"}, "settlement"),  # an Italian comune
        ({"region"}, "region"),
        (set[str](), None),
    ],
)
def test_place_type_precedence(types: set[str], expected: str | None) -> None:
    assert _place_type(types) == expected


def test_municipalities_read_as_settlements() -> None:
    assert ("settlement", "Q15284") in PLACE_CLASSES


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Point(16.3725 48.2083)", "48.2083,16.3725"),
        ("Point(-74.006 40.7127)", "40.7127,-74.006"),
        ("<http://www.wikidata.org/entity/Q405> Point(1 2)", None),  # on the Moon
    ],
)
def test_coordinates(value: str, expected: str | None) -> None:
    assert _coordinates(value) == expected


def test_queries_ask_for_the_hierarchy_and_walk_subclasses() -> None:
    for prop in ("wdt:P625", "wdt:P1566", "wdt:P297|wdt:P300", "wdt:P131", "wdt:P17"):
        assert prop in PLACE_QUERY
    assert "wdt:P31/wdt:P279* ?class" in PLACE_TYPE_QUERY
    for prop in ("wdt:P19", "wdt:P20", "wdt:P27"):
        assert prop in PLACE_ID_QUERY


def test_a_short_page_fails_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": [row("Q1741", "Vienna")]}})

    with pytest.raises(RuntimeError, match="1 of 2 requested places"):
        _fetch_place_page(mock_session(handler), ["Q1741", "Q40"])


def test_unknown_value_places_are_not_fetched() -> None:
    genid = {
        "type": "uri",
        "value": "http://www.wikidata.org/.well-known/genid/44bc0d50f9653c50af2e79df3e8bc126",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": [{"item": uri("Q1741")}, {"item": genid}]}})

    assert _fetch_place_qids(mock_session(handler)) == ["Q1741"]


def test_fetch_refuses_an_empty_population(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": []}})

    monkeypatch.setattr(WikidataPlacesAdapter, "open_session", lambda self: mock_session(handler))
    with pytest.raises(RuntimeError, match="no composer places"):
        list(WikidataPlacesAdapter().fetch())


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["wikidata_places"], WikidataPlacesAdapter)
