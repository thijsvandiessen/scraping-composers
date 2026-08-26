"""Tests for the Vienna Philharmonic HTTP fetch layer.

Retrying and client construction live in :mod:`composer_http` and are tested
there; what stays archive-specific is working out how many result fragments the
archive holds and requesting them.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from composer_http import PageCache
from composer_scrapers.wienerphil.fetch import (
    ARCHIVE_URL,
    fetch_detail,
    fetch_fragments,
    fetch_landing,
    page_count,
    total_item_count,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers.wienerphil.fetch.time.sleep", lambda _: None)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_total_item_count_reads_an_unquoted_attribute() -> None:
    # the count is served bare, without quotes around it
    assert total_item_count('<div id="totalItemCount" data-count=10749></div>') == 10749


def test_total_item_count_reads_a_quoted_attribute() -> None:
    assert total_item_count('<div id="totalItemCount" data-count="42"></div>') == 42


def test_total_item_count_raises_when_the_marker_is_gone() -> None:
    with pytest.raises(ValueError, match="totalItemCount"):
        total_item_count("<div>no count here</div>")


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 0), (1, 1), (1000, 1), (1001, 2), (10749, 11)],
)
def test_page_count_matches_the_sites_own_arithmetic(total: int, expected: int) -> None:
    assert page_count(total) == expected


def test_fetch_fragments_requests_one_page_per_thousand_concerts() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="fragment")

    landing = '<div id="totalItemCount" data-count=2500></div>'
    with _client(httpx.MockTransport(handler)) as client:
        assert list(fetch_fragments(client, landing)) == ["fragment"] * 3
    assert requested == [f"{ARCHIVE_URL}/{number}" for number in (1, 2, 3)]


def test_fetch_fragments_honours_max_pages() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="fragment")

    landing = '<div id="totalItemCount" data-count=10749></div>'
    with _client(httpx.MockTransport(handler)) as client:
        assert len(list(fetch_fragments(client, landing, max_pages=2))) == 2
    assert requested == [f"{ARCHIVE_URL}/1", f"{ARCHIVE_URL}/2"]


def test_fetch_landing_requests_the_archive_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ARCHIVE_URL
        return httpx.Response(200, text="<html>archive</html>")

    with _client(httpx.MockTransport(handler)) as client:
        assert fetch_landing(client) == "<html>archive</html>"


CONCERT_URL = "https://www.wienerphilharmoniker.at/en/konzerte/philharmonic-concert/2465/"


def test_fetch_detail_mirrors_the_page_it_fetched(tmp_path: Path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="<html>the concert</html>")

    cache = PageCache(tmp_path / "pages.db")
    with _client(httpx.MockTransport(handler)) as client:
        assert fetch_detail(client, CONCERT_URL, cache) == "<html>the concert</html>"
        # the second read is served from the mirror: an archive of concerts
        # already given does not change, so it is never fetched twice
        assert fetch_detail(client, CONCERT_URL, cache) == "<html>the concert</html>"
    assert requested == [CONCERT_URL]
    assert (cache.hits, cache.misses) == (1, 1)


def test_fetch_detail_without_a_cache_fetches_every_time() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="page")

    with _client(httpx.MockTransport(handler)) as client:
        assert fetch_detail(client, CONCERT_URL) == "page"
        assert fetch_detail(client, CONCERT_URL) == "page"
    assert requested == [CONCERT_URL, CONCERT_URL]


def test_one_unreachable_concert_does_not_end_the_sweep() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(httpx.MockTransport(handler)) as client:
        assert fetch_detail(client, CONCERT_URL) is None
