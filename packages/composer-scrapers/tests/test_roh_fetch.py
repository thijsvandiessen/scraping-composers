"""Tests for the roh HTTP layer — the mirror, the delay, and what is not mirrored."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from composer_http import PageCache
from composer_scrapers.roh import fetch as roh_fetch
from composer_scrapers.roh.fetch import REQUEST_DELAY_S, fetch_index, fetch_page, make_client
from composer_scrapers.roh.urls import index_url, work_url

WORK_URL = work_url(551)


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Swallow the politeness delay, recording what it would have been."""
    slept: list[float] = []
    monkeypatch.setattr(roh_fetch.time, "sleep", slept.append)
    return slept


def _client(handler: object, requested: list[str] | None = None) -> httpx.Client:
    def transport(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(str(request.url))
        return handler(request)  # type: ignore[operator]

    return httpx.Client(transport=httpx.MockTransport(transport))


# ---- the index ---- #


def test_a_letter_of_the_index_is_requested_by_its_own_url() -> None:
    requested: list[str] = []
    with _client(lambda _: httpx.Response(200, text="<table/>"), requested) as client:
        assert fetch_index(client, "0-9") == "<table/>"
    assert requested == [index_url("0-9")]


def test_an_index_letter_that_fails_raises_rather_than_shortening_the_sweep() -> None:
    """It is the only enumerator; a silent hole would look like a short run."""
    with _client(lambda _: httpx.Response(503)) as client, pytest.raises(httpx.HTTPError):
        fetch_index(client, "A")


def test_the_index_is_never_mirrored(tmp_path: Path) -> None:
    """Served from a mirror it would pin every later sweep to today's works."""
    cache = PageCache(tmp_path / "pages.db")
    with _client(lambda _: httpx.Response(200, text="<table/>")) as client:
        fetch_index(client, "A")
    assert cache.get(index_url("A")) is None


# ---- record pages ---- #


def test_a_record_page_is_fetched_once_and_served_from_the_mirror_after(tmp_path: Path) -> None:
    """A full sweep is ~18,000 pages and more than a day; it must be paid once."""
    cache = PageCache(tmp_path / "pages.db")
    requested: list[str] = []
    with _client(lambda _: httpx.Response(200, text="<html>work</html>"), requested) as client:
        assert fetch_page(client, WORK_URL, cache) == "<html>work</html>"
        assert fetch_page(client, WORK_URL, cache) == "<html>work</html>"
    assert requested == [WORK_URL]


def test_a_page_that_cannot_be_read_returns_none_instead_of_raising() -> None:
    """A sweep this long must not be lost to one 404 eighteen thousand pages in."""
    with _client(lambda _: httpx.Response(404)) as client:
        assert fetch_page(client, WORK_URL) is None


def test_a_failure_is_not_mirrored_so_the_next_run_retries_it(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    with _client(lambda _: httpx.Response(500)) as client:
        assert fetch_page(client, WORK_URL, cache) is None
    assert cache.get(WORK_URL) is None


def test_the_politeness_delay_is_paid_per_uncached_page(tmp_path: Path, no_delay: list[float]) -> None:
    cache = PageCache(tmp_path / "pages.db")
    with _client(lambda _: httpx.Response(200, text="<html/>")) as client:
        fetch_page(client, WORK_URL, cache)
        fetch_page(client, WORK_URL, cache)
    assert no_delay == [REQUEST_DELAY_S]


def test_the_delay_is_the_agreed_departure_from_the_stated_crawl_delay() -> None:
    """robots.txt asks 180s — three weeks for one sweep. See the fetch docstring."""
    assert REQUEST_DELAY_S == 5.0


# ---- the client ---- #


def test_the_client_follows_the_redirect_to_the_canonical_host() -> None:
    """An unfollowed 301 returns an empty body that parses as a page with no records."""
    with make_client() as client:
        assert client.follow_redirects is True


def test_the_client_identifies_itself_with_a_contact_address() -> None:
    with make_client() as client:
        assert "composer-ingest" in client.headers["User-Agent"]
