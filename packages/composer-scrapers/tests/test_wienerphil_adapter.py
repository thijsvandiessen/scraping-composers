"""Tests for the Vienna Philharmonic adapter's document output."""

from __future__ import annotations

import logging

import httpx
import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.wienerphil import WienerPhilAdapter
from test_wienerphil import FRAGMENT, LANDING

ARCHIVE_PATH = "/en/konzert-archiv"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers.wienerphil.fetch.time.sleep", lambda _: None)


def _serve(monkeypatch: pytest.MonkeyPatch, landing: str, fragment: str) -> list[str]:
    """Serve the archive page and one result fragment, recording the requests."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        body = landing if request.url.path == ARCHIVE_PATH else fragment
        return httpx.Response(200, text=body)

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
    assert [claim.object_label for claim in entities["Otto Nicolai"].claims] == ["conductor"]
    assert [claim.object_label for claim in entities["Jenny Lutzer"].claims] == ["soloist"]


def test_venues_are_not_emitted_as_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _landing_with(3), FRAGMENT)
    names = {doc.name for doc in WienerPhilAdapter().fetch() if isinstance(doc, EntityDocument)}
    # a venue is a column on the concert, not an entity
    assert "Musikverein, Golden Hall, Vienna, Austria" not in names


def test_max_pages_caps_the_fragments_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    requested = _serve(monkeypatch, _landing_with(10749), FRAGMENT)
    list(WienerPhilAdapter().fetch(max_pages=2))
    assert requested == [ARCHIVE_PATH, f"{ARCHIVE_PATH}/1", f"{ARCHIVE_PATH}/2"]


def test_a_short_read_is_reported(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    # the cheapest tripwire for the archive changing its page size or markup
    _serve(monkeypatch, _landing_with(999), FRAGMENT)
    with caplog.at_level(logging.WARNING):
        list(WienerPhilAdapter().fetch())
    assert "reports 999 concerts, parsed 3" in caplog.text
