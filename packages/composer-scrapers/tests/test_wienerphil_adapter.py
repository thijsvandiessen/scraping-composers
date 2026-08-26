"""Tests for the Vienna Philharmonic adapter's document output."""

from __future__ import annotations

import logging

import httpx
import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.wienerphil import WienerPhilAdapter
from test_wienerphil import FRAGMENT, LANDING
from test_wienerphil_details import _entry, _page

ARCHIVE_PATH = "/en/konzert-archiv"

#: What the fragment's concerts serve as their own detail page. 9001 has none,
#: which exercises the fallback to the listing's own reading of a concert.
DETAILS = {
    "/en/konzerte/philharmonic-concert/2465/": _page(
        _entry("Conductor", "Otto Nicolai"),
        _entry("Soprano", "Jenny Lutzer"),
    ),
    "/en/konzerte/5th-subscription-concert/8057/": _page(
        _entry("Musikalische Leitung", "Daniel Barenboim"),
    ),
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers.wienerphil.fetch.time.sleep", lambda _: None)


def _serve(monkeypatch: pytest.MonkeyPatch, landing: str, fragment: str) -> list[str]:
    """Serve the archive page, one result fragment and the detail pages.

    A concert with no entry in :data:`DETAILS` 404s, which is what a concert
    whose page cannot be read looks like to the adapter.
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requested.append(path)
        if path == ARCHIVE_PATH:
            return httpx.Response(200, text=landing)
        if path.startswith(f"{ARCHIVE_PATH}/"):
            return httpx.Response(200, text=fragment)
        if path in DETAILS:
            return httpx.Response(200, text=DETAILS[path])
        return httpx.Response(404)

    monkeypatch.setattr(
        "composer_scrapers.wienerphil._make_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return requested


def _landing_with(count: int) -> str:
    return LANDING + f'<div id="totalItemCount" data-count={count}></div>'


def test_wienerphil_is_registered() -> None:
    assert isinstance(REGISTRY["wienerphil"], WienerPhilAdapter)
    assert REGISTRY["wienerphil"].name == "wienerphil"


def test_fetch_yields_mentions_then_people(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    docs = list(WienerPhilAdapter().fetch())

    kinds = [type(doc) for doc in docs]
    mentions = [doc for doc in docs if isinstance(doc, WorkMentionDocument)]
    entities = [doc for doc in docs if isinstance(doc, EntityDocument)]
    # every mention comes before every entity: who conducted is only known
    # once all the concerts have been read
    assert kinds == [WorkMentionDocument] * len(mentions) + [EntityDocument] * len(entities)
    assert [mention.id for mention in mentions] == [
        "perf:2465:0",
        "perf:2465:1",
        "perf:8057:0",
        "perf:8057:1",
        "perf:8057:2",
        "perf:9001:0",
        "perf:9001:1",
        "perf:1264:0",
    ]


def test_mention_documents_carry_source_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    first = next(doc for doc in WienerPhilAdapter().fetch() if isinstance(doc, WorkMentionDocument))
    assert first.source_name == "wienerphil"
    assert first.composer == "Ludwig van Beethoven"
    assert first.url == "https://www.wienerphilharmoniker.at/en/konzerte/philharmonic-concert/2465/"


def test_people_come_from_the_filter_vocabularies(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    entities = [doc for doc in WienerPhilAdapter().fetch() if isinstance(doc, EntityDocument)]
    assert [(doc.name, doc.kind) for doc in entities] == [
        ("Ludwig van Beethoven", "person"),
        ("Arnold Schönberg", "person"),
        ("Otto Nicolai", "person"),
        ("Jenny Lutzer", "person"),
        ("Vienna Philharmonic", "ensemble"),
    ]


def test_conducting_in_the_archive_decides_the_profession(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    entities = {doc.name: doc for doc in WienerPhilAdapter().fetch() if isinstance(doc, EntityDocument)}
    # Nicolai conducted one of the fragment's concerts; Lutzer only performed
    professions = {
        name: [claim.object_label for claim in doc.claims if claim.predicate == "has_profession"]
        for name, doc in entities.items()
    }
    assert professions["Otto Nicolai"] == ["conductor"]
    assert professions["Jenny Lutzer"] == ["soloist"]


def test_venues_are_not_emitted_as_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    names = {doc.name for doc in WienerPhilAdapter().fetch() if isinstance(doc, EntityDocument)}
    # a venue is a column on the concert, not an entity
    assert "Musikverein, Golden Hall, Vienna, Austria" not in names


def test_max_pages_caps_the_concerts_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # the request-per-record half of this source is the detail pages, so that is
    # what the cap counts — and only the one fragment holding them is fetched
    requested = _serve(monkeypatch, _landing_with(10749), FRAGMENT)
    mentions = [doc for doc in WienerPhilAdapter().fetch(max_pages=2) if isinstance(doc, WorkMentionDocument)]
    assert {mention.id.split(":")[1] for mention in mentions} == {"2465", "8057"}
    assert requested == [
        ARCHIVE_PATH,
        f"{ARCHIVE_PATH}/1",
        "/en/konzerte/philharmonic-concert/2465/",
        "/en/konzerte/5th-subscription-concert/8057/",
    ]


def test_mentions_carry_the_disciplines_only_the_detail_page_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    mentions = [doc for doc in WienerPhilAdapter().fetch() if isinstance(doc, WorkMentionDocument)]
    detailed = next(mention for mention in mentions if mention.raw["concert_id"] == "2465")
    assert detailed.raw["soloists"] == [{"name": "Jenny Lutzer", "discipline": "Soprano"}]
    assert detailed.raw["credits"] == [["Conductor", "Otto Nicolai"], ["Soprano", "Jenny Lutzer"]]


def test_a_concert_whose_page_cannot_be_read_keeps_the_listings_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    mentions = [doc for doc in WienerPhilAdapter().fetch() if isinstance(doc, WorkMentionDocument)]
    plain = [mention for mention in mentions if mention.raw["concert_id"] == "9001"]
    assert [mention.composer for mention in plain] == ["Franz Schubert", "Anton Bruckner"]
    assert plain[0].raw["credits"] == []


def test_a_discipline_seen_on_a_concert_becomes_a_performs_as_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    entities = {doc.name: doc for doc in WienerPhilAdapter().fetch() if isinstance(doc, EntityDocument)}
    assert [
        (claim.predicate, claim.object_label or claim.value) for claim in entities["Jenny Lutzer"].claims
    ] == [
        ("has_profession", "soloist"),
        ("performs_as", "Soprano"),
    ]


def test_a_short_read_is_reported(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    # the cheapest tripwire for the archive changing its page size or markup
    _serve(monkeypatch, _landing_with(999), FRAGMENT)
    with caplog.at_level(logging.WARNING):
        list(WienerPhilAdapter().fetch())
    assert "reports 999 concerts, parsed 4" in caplog.text
