"""Tests for the Wikidata ensembles adapter: orchestras and choirs, the anchors of #350."""

import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from composer_http import PageCache
from composer_http.testing import mock_session
from composer_scrapers import REGISTRY, SourceClaim
from composer_scrapers.wikidata import WikidataEnsemblesAdapter
from composer_scrapers.wikidata.ensembles import (
    ENSEMBLE_ID_QUERY,
    ENSEMBLE_MULTI_QUERY,
    ENSEMBLE_QUERY,
    _documents_from_rows,
    _fetch_ensemble_page,
    _fetch_ensemble_qids,
    ensemble_type,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
ORCHESTRA, CHOIR = "Q42998", "Q131186"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}


def lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def row(qid: str, label: str | None = None, **vars: str) -> dict[str, Any]:
    """A result row; a value written "Q1=Label" binds an item and its label,
    any other value a literal."""
    bindings: dict[str, Any] = {"item": uri(qid)}
    if label is not None:
        bindings["itemLabel"] = lit(label)
    for var, value in vars.items():
        if "=" in value:
            other, other_label = value.split("=", 1)
            bindings[var] = uri(other)
            bindings[f"{var}Label"] = lit(other_label)
        else:
            bindings[var] = lit(value)
    return bindings


def root(qid: str, cls: str) -> dict[str, Any]:
    return {"item": uri(qid), "root": uri(cls)}


def claims_of(rows: list[dict[str, Any]], population: set[str] | None = None) -> tuple[SourceClaim, ...]:
    (document,) = _documents_from_rows(rows, population or set(), NOW)
    return document.claims


def test_an_orchestra_becomes_a_qid_pinned_ensemble_document() -> None:
    rows = [
        row("Q1", "Vienna Philharmonic", hq="Q1741=Vienna", hqSettlement="true", musicbrainz="mb-1"),
        root("Q1", ORCHESTRA),
        {"item": uri("Q1"), "name": lit("Wiener Philharmoniker")},
    ]
    (document,) = _documents_from_rows(rows, {"Q1"}, NOW)

    assert document.id == "Q1"
    assert document.url == "https://www.wikidata.org/wiki/Q1"
    assert document.kind == "ensemble"
    assert document.source_name == "wikidata_ensembles"
    assert document.name == "Vienna Philharmonic"
    assert set(document.claims) == {
        SourceClaim("ensemble_type", value="orchestra"),
        SourceClaim("based_in", "place", "Vienna"),
        SourceClaim("also_known_as", value="Wiener Philharmoniker"),
        SourceClaim("musicbrainz_id", value="mb-1"),
    }
    assert document.raw["based_in"] == {"item": "Q1741", "label": "Vienna"}
    assert document.raw["classes"] == [ORCHESTRA]


@pytest.mark.parametrize(
    ("classes", "label", "expected"),
    [
        ({ORCHESTRA, CHOIR}, "Choir and Orchestra", "choir"),
        ({ORCHESTRA}, "Some Orchestra", "orchestra"),
        (set(), "Hagen Quartet", "chamber_ensemble"),
        ({ORCHESTRA}, "Hagen Quartet", "orchestra"),
        (set(), "Ensemble Intercontemporain", "other"),
    ],
)
def test_the_type_comes_from_the_classes_then_the_name(classes: set[str], label: str, expected: str) -> None:
    assert ensemble_type(classes, label) == expected


def test_a_headquarters_that_is_a_settlement_is_the_home_city() -> None:
    rows = [row("Q1", "Orchestra", hq="Q1741=Vienna", hqSettlement="true", hqAdmin="Q40=Austria")]
    assert SourceClaim("based_in", "place", "Vienna") in claims_of(rows)


def test_a_headquarters_that_is_a_building_is_read_through_its_admin_area() -> None:
    rows = [row("Q1", "Orchestra", hq="Q2=Musikverein", hqSettlement="false", hqAdmin="Q1741=Vienna")]
    based_in = [c for c in claims_of(rows) if c.predicate == "based_in"]
    assert based_in == [SourceClaim("based_in", "place", "Vienna")]


def test_an_unnamed_home_city_is_skipped() -> None:
    rows = [row("Q1", "Orchestra", hq="Q2=Q2", hqSettlement="true")]
    assert not [c for c in claims_of(rows) if c.predicate == "based_in"]


def test_every_name_in_every_language_is_an_alias_once() -> None:
    rows = [row("Q1", "Vienna Philharmonic")] + [
        {"item": uri("Q1"), "name": lit(name)}
        for name in ("Vienna Philharmonic", "vienna philharmonic", "Wiener Philharmoniker", "VPO")
    ]
    aliases = [c.value for c in claims_of(rows) if c.predicate == "also_known_as"]
    assert aliases == ["VPO", "Wiener Philharmoniker"]


def test_part_of_is_kept_only_within_the_population() -> None:
    rows = [
        row("Q1", "Vienna State Opera Orchestra"),
        row("Q1", "Vienna State Opera Orchestra", partOf="Q2=Some Orchestra Federation"),
        row("Q1", "Vienna State Opera Orchestra", partOf="Q3=Vienna State Opera"),
    ]
    (document,) = _documents_from_rows(rows, {"Q1", "Q2"}, NOW)
    part_of = [c for c in document.claims if c.predicate == "part_of"]
    assert part_of == [
        SourceClaim("part_of", "ensemble", "Some Orchestra Federation", object_wikidata_id="Q2")
    ]
    assert [p["item"] for p in document.raw["part_of"]] == ["Q2", "Q3"]


def test_an_unlabelled_item_is_skipped() -> None:
    assert _documents_from_rows([row("Q1", "Q1")], {"Q1"}, NOW) == []


def test_the_queries_ask_for_the_anchoring_properties() -> None:
    assert f"wd:{ORCHESTRA}" in ENSEMBLE_ID_QUERY and f"wd:{CHOIR}" in ENSEMBLE_ID_QUERY
    assert "wdt:P159" in ENSEMBLE_QUERY and "wdt:P131" in ENSEMBLE_QUERY and "wdt:P434" in ENSEMBLE_QUERY
    assert "skos:altLabel" in ENSEMBLE_MULTI_QUERY and "wdt:P361" in ENSEMBLE_MULTI_QUERY


def test_fetch_ensemble_qids_orders_numerically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert urllib.parse.parse_qs(request.read().decode())["query"][0] == ENSEMBLE_ID_QUERY
        bindings = [{"item": uri(q)} for q in ("Q100", "Q20", "Q3")]
        return httpx.Response(200, json={"results": {"bindings": bindings}})

    with mock_session(handler) as session:
        assert _fetch_ensemble_qids(session) == ["Q3", "Q20", "Q100"]


def test_a_short_page_fails_the_run_and_is_not_mirrored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(urllib.parse.parse_qs(request.read().decode())["query"][0])
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "A")]}})

    session = mock_session(handler, cache=PageCache(tmp_path / "pages.db"))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="1 of 2 requested ensembles"):
            _fetch_ensemble_page(session, ["Q1", "Q2"])
    assert len(asked) == 2


def test_a_complete_page_is_mirrored(tmp_path: Path) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(urllib.parse.parse_qs(request.read().decode())["query"][0])
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "A")]}})

    session = mock_session(handler, cache=PageCache(tmp_path / "pages.db"))
    for _ in range(2):
        _fetch_ensemble_page(session, ["Q1"])
    assert len(asked) == 2  # detail + multi, once each


def test_fetch_pages_through_the_population(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        if query == ENSEMBLE_ID_QUERY:
            return httpx.Response(200, json={"results": {"bindings": [{"item": uri("Q1")}]}})
        if "?root" in query:
            return httpx.Response(200, json={"results": {"bindings": [root("Q1", CHOIR)]}})
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "Arnold Schoenberg Chor")]}})

    monkeypatch.setattr(WikidataEnsemblesAdapter, "open_session", lambda self: mock_session(handler))
    (document,) = WikidataEnsemblesAdapter().fetch()
    assert SourceClaim("ensemble_type", value="choir") in document.claims


def test_fetch_refuses_an_empty_population(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": []}})

    monkeypatch.setattr(WikidataEnsemblesAdapter, "open_session", lambda self: mock_session(handler))
    with pytest.raises(RuntimeError, match="no ensemble ids"):
        list(WikidataEnsemblesAdapter().fetch())


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["wikidata_ensembles"], WikidataEnsemblesAdapter)
