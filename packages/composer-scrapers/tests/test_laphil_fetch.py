"""Tests for the laphil HTTP layer."""

from __future__ import annotations

import httpx
import pytest
from composer_http import PageCache
from composer_scrapers.laphil import fetch as laphil_fetch
from composer_scrapers.laphil.fetch import SITEMAP_URL, fetch_page, fetch_sitemap, make_client


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(laphil_fetch.time, "sleep", lambda _: None)


def _client(handler: object, requested: list[str] | None = None) -> httpx.Client:
    def transport(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(str(request.url))
        return handler(request)  # type: ignore[operator]

    return httpx.Client(transport=httpx.MockTransport(transport))


def test_fetch_sitemap_requests_the_sitemap() -> None:
    requested: list[str] = []
    with _client(lambda _: httpx.Response(200, text="<urlset/>"), requested) as client:
        assert fetch_sitemap(client) == "<urlset/>"
    assert requested == [SITEMAP_URL]


def test_fetch_page_returns_the_body() -> None:
    with _client(lambda _: httpx.Response(200, text="<html>brahms</html>")) as client:
        assert fetch_page(client, "https://www.laphil.com/people/johannes-brahms") == "<html>brahms</html>"


def test_fetch_page_returns_none_rather_than_raising() -> None:
    """One 404 in a walk of thousands of pages must not end the sweep."""
    with _client(lambda _: httpx.Response(404)) as client:
        assert fetch_page(client, "https://www.laphil.com/events/gone") is None


def test_fetch_page_serves_a_mirrored_page_without_a_request(tmp_path: object) -> None:
    cache = PageCache(tmp_path / "pages.db")  # type: ignore[operator]
    cache.put("https://www.laphil.com/events/brahms-4", "<html>mirrored</html>")
    requested: list[str] = []
    with _client(lambda _: httpx.Response(200, text="<html>fetched</html>"), requested) as client:
        page = fetch_page(client, "https://www.laphil.com/events/brahms-4", cache)
    assert page == "<html>mirrored</html>"
    assert requested == []


def test_fetch_page_mirrors_what_it_fetches(tmp_path: object) -> None:
    """The mirror is what lets a later pass re-read this HTML for more than
    composers without going back to the network."""
    cache = PageCache(tmp_path / "pages.db")  # type: ignore[operator]
    url = "https://www.laphil.com/events/brahms-4"
    with _client(lambda _: httpx.Response(200, text="<html>fetched</html>")) as client:
        fetch_page(client, url, cache)
    assert cache.get(url) == "<html>fetched</html>"


def test_fetch_page_does_not_mirror_a_failure(tmp_path: object) -> None:
    cache = PageCache(tmp_path / "pages.db")  # type: ignore[operator]
    url = "https://www.laphil.com/events/gone"
    with _client(lambda _: httpx.Response(404)) as client:
        assert fetch_page(client, url, cache) is None
    assert cache.get(url) is None


def test_fetch_page_sleeps_only_when_it_actually_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(laphil_fetch.time, "sleep", slept.append)
    cache = PageCache(tmp_path / "pages.db")  # type: ignore[operator]
    url = "https://www.laphil.com/events/brahms-4"
    with _client(lambda _: httpx.Response(200, text="<html/>")) as client:
        fetch_page(client, url, cache)
        fetch_page(client, url, cache)
    assert slept == [laphil_fetch.REQUEST_DELAY_S]


def test_client_identifies_the_scraper() -> None:
    with make_client() as client:
        assert "test-contact@example.com" in client.headers["User-Agent"]


def test_fetch_page_follows_a_past_events_redirect() -> None:
    """A past event 302s from /events/<slug> to its /events/instances/… permalink,
    which serves the same page. Not following it loses the whole archive."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/events/ax-kavakos-ma-1":
            return httpx.Response(
                302, headers={"Location": "/events/instances/krg/2023-01-28/ax-kavakos-ma-1"}
            )
        return httpx.Response(200, text="<html>programme</html>")

    with _client(handler) as client:
        client.follow_redirects = True
        assert fetch_page(client, "https://www.laphil.com/events/ax-kavakos-ma-1") == "<html>programme</html>"


def test_make_client_follows_redirects() -> None:
    with make_client() as client:
        assert client.follow_redirects
