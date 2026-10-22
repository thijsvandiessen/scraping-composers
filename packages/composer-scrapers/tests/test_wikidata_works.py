"""Tests for the Wikidata premieres adapter: works with a date of first performance."""

import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from composer_scrapers import REGISTRY
from composer_scrapers.wikidata import WikidataWorksAdapter
from composer_scrapers.wikidata.works import (
    WORK_ID_QUERY,
    WORK_QUERY,
    _fetch_work_page,
    _fetch_work_qids,
    _mentions_from_rows,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}


def row(qid: str, label: str | None, composer: str | None = None, **vars: str) -> dict[str, Any]:
    """A WORK_QUERY result row; ``composer`` is "QID=Label", ``vars`` literals."""
    bindings: dict[str, Any] = {"item": uri(qid)}
    if label is not None:
        bindings["itemLabel"] = {"type": "literal", "value": label}
    if composer is not None:
        composer_qid, composer_label = composer.split("=")
        bindings["composer"] = uri(composer_qid)
        bindings["composerLabel"] = {"type": "literal", "value": composer_label}
    for var, value in vars.items():
        bindings[var] = {"type": "literal", "value": value}
    return bindings


NINTH = "Q4895=Ludwig van Beethoven"


def test_a_work_becomes_a_mention_carrying_its_premiere() -> None:
    rows = [
        row(
            "Q151",
            "Symphony No. 9",
            NINTH,
            premiere="1824-05-07T00:00:00Z",
            premierePrecision="11",
            premierePlaceLabel="Theater am Kärntnertor",
        )
    ]
    (mention,) = _mentions_from_rows(rows, NOW)

    assert mention.id == "Q151:Q4895"
    assert mention.url == "https://www.wikidata.org/wiki/Q151"
    assert mention.source_name == "wikidata_works"
    assert mention.title == "Symphony No. 9"
    assert mention.composer == "Ludwig van Beethoven"
    assert mention.raw["premiere"] == {"date": "1824-05-07", "place": "Theater am Kärntnertor"}
    assert mention.raw["composer_item"] == "http://www.wikidata.org/entity/Q4895"


def test_the_premiere_date_keeps_its_recorded_precision() -> None:
    rows = [row("Q1", "Cantata", NINTH, premiere="1724-01-01T00:00:00Z", premierePrecision="9")]
    (mention,) = _mentions_from_rows(rows, NOW)
    assert mention.raw["premiere"] == {"date": "1724", "place": None}


def test_of_several_premiere_dates_the_earliest_wins() -> None:
    rows = [
        row("Q1", "Opera", NINTH, premiere="1850-03-01T00:00:00Z", premierePrecision="11"),
        row("Q1", "Opera", NINTH, premiere="1849-11-20T00:00:00Z", premierePrecision="11"),
    ]
    (mention,) = _mentions_from_rows(rows, NOW)
    assert mention.raw["premiere"]["date"] == "1849-11-20"
    assert mention.raw["premiere_dates"] == ["1849-11-20", "1850-03-01"]


def test_the_more_precise_of_two_agreeing_dates_wins() -> None:
    rows = [
        row("Q1", "Opera", NINTH, premiere="1850-01-01T00:00:00Z", premierePrecision="9"),
        row("Q1", "Opera", NINTH, premiere="1850-03-01T00:00:00Z", premierePrecision="11"),
    ]
    (mention,) = _mentions_from_rows(rows, NOW)
    assert mention.raw["premiere"]["date"] == "1850-03-01"


def test_several_places_are_kept_together() -> None:
    rows = [
        row("Q1", "Opera", NINTH, premiere="1850-03-01T00:00:00Z", premierePlaceLabel="Weimar"),
        row(
            "Q1",
            "Opera",
            NINTH,
            premiere="1850-03-01T00:00:00Z",
            premierePlaceLabel="Deutsches Nationaltheater",
        ),
    ]
    (mention,) = _mentions_from_rows(rows, NOW)
    assert mention.raw["premiere"]["place"] == "Deutsches Nationaltheater; Weimar"


def test_a_collaboration_gets_one_mention_per_composer() -> None:
    rows = [
        row("Q1", "F-A-E Sonata", "Q7351=Robert Schumann", premiere="1853-10-28T00:00:00Z"),
        row("Q1", "F-A-E Sonata", "Q7294=Johannes Brahms", premiere="1853-10-28T00:00:00Z"),
    ]
    mentions = _mentions_from_rows(rows, NOW)
    assert [(m.id, m.composer) for m in mentions] == [
        ("Q1:Q7294", "Johannes Brahms"),
        ("Q1:Q7351", "Robert Schumann"),
    ]


def test_unnameable_works_and_composers_are_skipped() -> None:
    rows = [
        row("Q1", "Q1", NINTH, premiere="1850-03-01T00:00:00Z"),  # label service echoed the QID
        row("Q2", "Opera", "Q99=Q99", premiere="1850-03-01T00:00:00Z"),
        row("Q3", "Opera", NINTH, premierePlaceLabel="Q42"),  # unknown-value date, unnamed place
    ]
    assert _mentions_from_rows(rows, NOW) == []


def test_fetch_work_page_binds_the_batch_and_requires_every_work() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.content.decode())["query"][0]
        assert "VALUES ?item { wd:Q1 wd:Q2 }" in query
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "A", NINTH)]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="1 of 2 requested works"):
            _fetch_work_page(client, ["Q1", "Q2"])


def test_fetch_work_qids_orders_numerically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert urllib.parse.parse_qs(request.content.decode())["query"][0] == WORK_ID_QUERY
        bindings = [{"item": uri(q)} for q in ("Q100", "Q20", "Q3", "Q20")]
        return httpx.Response(200, json={"results": {"bindings": bindings}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _fetch_work_qids(client) == ["Q3", "Q20", "Q100"]


def test_queries_ask_for_the_first_performance_properties() -> None:
    assert "wdt:P1191" in WORK_ID_QUERY and "wdt:P86" in WORK_ID_QUERY
    assert "wdt:P4647" in WORK_QUERY and "timePrecision" in WORK_QUERY


def test_fetch_pages_through_the_population(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers.wikidata.works.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.content.decode())["query"][0]
        if query == WORK_ID_QUERY:
            return httpx.Response(200, json={"results": {"bindings": [{"item": uri("Q151")}]}})
        bindings = [row("Q151", "Symphony No. 9", NINTH, premiere="1824-05-07T00:00:00Z")]
        return httpx.Response(200, json={"results": {"bindings": bindings}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "composer_scrapers.wikidata.works.new_client", lambda **_: httpx.Client(transport=transport)
    )
    (mention,) = WikidataWorksAdapter().fetch()
    assert mention.raw["premiere"]["date"] == "1824-05-07"


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["wikidata_works"], WikidataWorksAdapter)
