"""Tests for the laphil HTTP layer."""

from __future__ import annotations

import httpx
import pytest
from composer_http import PageCache, SourceSession
from composer_http.testing import mock_session
from composer_scrapers.laphil import LaPhilAdapter
from composer_scrapers.laphil.fetch import SITEMAP_URL, fetch_page, fetch_sitemap


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _client(
    handler: object, requested: list[str] | None = None, cache: PageCache | None = None
) -> SourceSession:
    def transport(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(str(request.url))
        return handler(request)  # type: ignore[operator]

    return mock_session(transport, cache=cache)


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
    with _client(
        lambda _: httpx.Response(200, text="<html>fetched</html>"), requested, cache=cache
    ) as client:
        page = fetch_page(client, "https://www.laphil.com/events/brahms-4")
    assert page == "<html>mirrored</html>"
    assert requested == []


def test_fetch_page_mirrors_what_it_fetches(tmp_path: object) -> None:
    """The mirror is what lets a later pass re-read this HTML for more than
    composers without going back to the network."""
    cache = PageCache(tmp_path / "pages.db")  # type: ignore[operator]
    url = "https://www.laphil.com/events/brahms-4"
    with _client(lambda _: httpx.Response(200, text="<html>fetched</html>"), cache=cache) as client:
        fetch_page(client, url)
    assert cache.get(url) == "<html>fetched</html>"


def test_fetch_page_does_not_mirror_a_failure(tmp_path: object) -> None:
    cache = PageCache(tmp_path / "pages.db")  # type: ignore[operator]
    url = "https://www.laphil.com/events/gone"
    with _client(lambda _: httpx.Response(404), cache=cache) as client:
        assert fetch_page(client, url) is None
    assert cache.get(url) is None


def test_client_identifies_the_scraper() -> None:
    with LaPhilAdapter().open_session() as session:
        assert "test-contact@example.com" in session.client.headers["User-Agent"]


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
        client.client.follow_redirects = True
        assert fetch_page(client, "https://www.laphil.com/events/ax-kavakos-ma-1") == "<html>programme</html>"


def test_make_client_follows_redirects() -> None:
    with LaPhilAdapter().open_session() as session:
        assert session.client.follow_redirects
