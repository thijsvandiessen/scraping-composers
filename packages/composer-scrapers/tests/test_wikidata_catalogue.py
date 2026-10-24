"""Tests for the Wikidata catalogue adapter: works with a catalogue code (#351)."""

import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from composer_http.testing import mock_session
from composer_scrapers import REGISTRY
from composer_scrapers.wikidata import WikidataCatalogueAdapter
from composer_scrapers.wikidata.catalogue import (
    CATALOGUE_ID_QUERY,
    CATALOGUE_QUERY,
    _fetch_catalogue_page,
    _fetch_catalogue_qids,
    _mentions_from_rows,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
ENTITY = "http://www.wikidata.org/entity/"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"{ENTITY}{qid}"}


def lit(value: str, lang: str | None = None) -> dict[str, str]:
    binding = {"type": "literal", "value": value}
    if lang:
        binding["xml:lang"] = lang
    return binding


def row(
    qid: str,
    label: str | None = "Symphony No. 40",
    composer: str = "Q254=Wolfgang Amadeus Mozart",
    **vars: Any,
) -> dict[str, Any]:
    """A CATALOGUE_QUERY row; ``vars`` are literals, or a QID for the entity variables."""
    composer_qid, composer_label = composer.split("=")
    bindings: dict[str, Any] = {
        "item": uri(qid),
        "composer": uri(composer_qid),
        "composerLabel": lit(composer_label),
    }
    if label is not None:
        bindings["itemLabel"] = lit(label)
    for var, value in vars.items():
        bindings[var] = uri(value) if var in ("catalogue", "partOf") else lit(value)
    return bindings


def test_a_work_carries_its_codes_external_ids_and_parents() -> None:
    rows = [
        row("Q2", code="550", catalogue="Q162476", catalogueLabel="Köchel catalogue", catalogueShort="KV"),
        row("Q2", code="K. 550", catalogue="Q162476", catalogueLabel="Köchel catalogue", catalogueShort="K."),
        row("Q2", musicbrainz="0c5b4f7e-1d4e-3c0b-9c2b-2d1f2e5a6b7c"),
        row("Q2", iswc="T-034.524.680-1"),
        row("Q2", partOf="Q99"),
    ]
    (mention,) = _mentions_from_rows(rows, NOW)

    assert mention.id == "Q2:Q254"
    assert mention.source_name == "wikidata_catalogue"
    assert mention.url == "https://www.wikidata.org/wiki/Q2"
    assert (mention.title, mention.composer) == ("Symphony No. 40", "Wolfgang Amadeus Mozart")
    assert mention.raw["item"] == f"{ENTITY}Q2"
    assert mention.raw["composer_item"] == f"{ENTITY}Q254"
    assert mention.raw["catalogue_codes"] == [
        {
            "catalogue_item": "Q162476",
            "code": "550",
            "catalogue": "Köchel catalogue",
            "catalogue_short": "KV",
        },
        {
            "catalogue_item": "Q162476",
            "code": "K. 550",
            "catalogue": "Köchel catalogue",
            "catalogue_short": "K.",
        },
    ]
    assert mention.raw["musicbrainz_work"] == ["0c5b4f7e-1d4e-3c0b-9c2b-2d1f2e5a6b7c"]
    assert mention.raw["iswc"] == ["T-034.524.680-1"]
    assert mention.raw["part_of"] == [f"{ENTITY}Q99"]


def test_one_short_name_is_kept_however_many_languages_state_one() -> None:
    rows = [
        row("Q1", code="1048", catalogue="Q5", catalogueShort="BWV"),
        row("Q1", code="1048", catalogue="Q5", catalogueShort="B.W.V."),
    ]
    (mention,) = _mentions_from_rows(rows, NOW)
    assert [c["catalogue_short"] for c in mention.raw["catalogue_codes"]] == ["B.W.V."]


def test_a_collaboration_gets_one_mention_per_composer() -> None:
    rows = [
        row("Q1", "F-A-E Sonata", "Q7351=Robert Schumann", code="WoO 2"),
        row("Q1", "F-A-E Sonata", "Q7294=Johannes Brahms", code="WoO 2"),
    ]
    assert [(m.id, m.composer) for m in _mentions_from_rows(rows, NOW)] == [
        ("Q1:Q7294", "Johannes Brahms"),
        ("Q1:Q7351", "Robert Schumann"),
    ]


def test_unnameable_works_and_composers_are_skipped() -> None:
    rows = [row("Q1", "Q1", code="1"), row("Q2", None, code="2"), row("Q3", "Sonata", "Q9=Q9", code="3")]
    assert _mentions_from_rows(rows, NOW) == []


def test_queries_ask_for_the_identity_properties() -> None:
    assert "wdt:P86" in CATALOGUE_ID_QUERY and "pq:P972" in CATALOGUE_ID_QUERY
    for prop in ("P528", "P972", "P1813", "P435", "P1827", "P361"):
        assert prop in CATALOGUE_QUERY


def test_fetch_catalogue_page_requires_every_work() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        assert "VALUES ?item { wd:Q1 wd:Q2 }" in query
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", code="1")]}})

    with mock_session(handler) as session:
        with pytest.raises(RuntimeError, match="1 of 2 requested works"):
            _fetch_catalogue_page(session, ["Q1", "Q2"])


def test_fetch_catalogue_qids_orders_numerically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert urllib.parse.parse_qs(request.read().decode())["query"][0] == CATALOGUE_ID_QUERY
        bindings = [{"item": uri(q)} for q in ("Q100", "Q20", "Q3", "Q20")]
        return httpx.Response(200, json={"results": {"bindings": bindings}})

    with mock_session(handler) as session:
        assert _fetch_catalogue_qids(session) == ["Q3", "Q20", "Q100"]


def _fake_wdqs(monkeypatch: pytest.MonkeyPatch, population: list[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        if "VALUES" not in query:
            return httpx.Response(200, json={"results": {"bindings": [{"item": uri(q)} for q in population]}})
        qids = re.findall(r"wd:(Q\d+)", query.split("VALUES", 1)[1].split("}", 1)[0])
        rows = [row(qid, f"Work {qid}", code=qid.removeprefix("Q")) for qid in qids]
        return httpx.Response(200, json={"results": {"bindings": rows}})

    monkeypatch.setattr(WikidataCatalogueAdapter, "open_session", lambda self: mock_session(handler))


def test_fetch_pages_through_the_population(monkeypatch: pytest.MonkeyPatch) -> None:
    population = [f"Q{n}" for n in range(1, 1201)]
    _fake_wdqs(monkeypatch, population)

    fetched = [mention.id for mention in WikidataCatalogueAdapter().fetch()]
    assert fetched == [f"{qid}:Q254" for qid in population]


def test_max_pages_caps_the_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_wdqs(monkeypatch, [f"Q{n}" for n in range(1, 1201)])
    assert len(list(WikidataCatalogueAdapter().fetch(max_pages=1))) == 500


def test_fetch_refuses_an_empty_population(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_wdqs(monkeypatch, [])
    with pytest.raises(RuntimeError, match="no works with a catalogue code"):
        list(WikidataCatalogueAdapter().fetch())


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["wikidata_catalogue"], WikidataCatalogueAdapter)
