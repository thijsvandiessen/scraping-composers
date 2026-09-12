"""Tests for the harmoniamundi HTTP layer."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from composer_http import PageCache
from composer_scrapers.harmoniamundi import fetch as hm_fetch
from composer_scrapers.harmoniamundi.fetch import (
    fetch_page,
    fetch_sitemap_index,
    fetch_urlset,
    make_client,
)
from composer_scrapers.harmoniamundi.urls import SITEMAP_INDEX_URL

ALBUM_URL = "https://www.harmoniamundi.com/en/albums/bach-js-chaconnes/"


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hm_fetch.time, "sleep", lambda _: None)


def _client(handler: object, requested: list[str] | None = None) -> httpx.Client:
    def transport(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(str(request.url))
        return handler(request)  # type: ignore[operator]

    return httpx.Client(transport=httpx.MockTransport(transport))


# ---- the sitemaps ---- #


def test_the_sitemap_index_is_requested_at_the_site_root() -> None:
    requested: list[str] = []
    with _client(lambda _: httpx.Response(200, text="<sitemapindex/>"), requested) as client:
        assert fetch_sitemap_index(client) == "<sitemapindex/>"
    assert requested == [SITEMAP_INDEX_URL]


def test_a_urlset_is_fetched_by_its_own_url() -> None:
    requested: list[str] = []
    url = "https://www.harmoniamundi.com/albums-sitemap3.xml"
    with _client(lambda _: httpx.Response(200, text="<urlset/>"), requested) as client:
        assert fetch_urlset(client, url) == "<urlset/>"
    assert requested == [url]


def test_a_sitemap_is_never_mirrored(tmp_path: Path) -> None:
    """Served from a mirror it would pin the sweep to the albums that existed first."""
    cache = PageCache(tmp_path / "pages.db")
    with _client(lambda _: httpx.Response(200, text="<urlset/>")) as client:
        fetch_sitemap_index(client)
        fetch_urlset(client, "https://www.harmoniamundi.com/albums-sitemap.xml")
    assert cache.get(SITEMAP_INDEX_URL) is None


# ---- the pages ---- #


def test_a_page_comes_back_as_its_body() -> None:
    with _client(lambda _: httpx.Response(200, text="<html>bach</html>")) as client:
        assert fetch_page(client, ALBUM_URL) == "<html>bach</html>"


def test_a_missing_page_is_none_rather_than_an_exception() -> None:
    """One 404 in a sweep of ~2000 pages must not end the run."""
    with _client(lambda _: httpx.Response(404)) as client:
        assert fetch_page(client, ALBUM_URL) is None


def test_a_mirrored_page_is_served_without_a_request(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put(ALBUM_URL, "<html>mirrored</html>")
    requested: list[str] = []
    with _client(lambda _: httpx.Response(200, text="<html>fetched</html>"), requested) as client:
        assert fetch_page(client, ALBUM_URL, cache) == "<html>mirrored</html>"
    assert requested == []


def test_a_fetched_page_is_mirrored(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    with _client(lambda _: httpx.Response(200, text="<html>bach</html>")) as client:
        fetch_page(client, ALBUM_URL, cache)
    assert cache.get(ALBUM_URL) == "<html>bach</html>"


def test_a_page_that_could_not_be_fetched_is_not_mirrored(tmp_path: Path) -> None:
    """Caching the failure would make the next run skip a page that may come back."""
    cache = PageCache(tmp_path / "pages.db")
    with _client(lambda _: httpx.Response(500)) as client:
        assert fetch_page(client, ALBUM_URL, cache) is None
    assert cache.get(ALBUM_URL) is None


def test_the_delay_is_slept_only_when_a_request_actually_happened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(hm_fetch.time, "sleep", lambda seconds: slept.append(seconds))
    cache = PageCache(tmp_path / "pages.db")
    with _client(lambda _: httpx.Response(200, text="<html>bach</html>")) as client:
        fetch_page(client, ALBUM_URL, cache)
        assert slept == [hm_fetch.REQUEST_DELAY_S]
        fetch_page(client, ALBUM_URL, cache)
    assert slept == [hm_fetch.REQUEST_DELAY_S]


# ---- the client ---- #


def test_the_client_follows_redirects() -> None:
    """A slug without its trailing slash answers 301, and `get_text` only raises on
    4xx and 5xx — so with redirects off the sweep would read an empty body as
    "not an album page" rather than as an error."""
    with make_client() as client:
        assert client.follow_redirects is True


def test_the_client_advertises_a_contact_address() -> None:
    with make_client() as client:
        assert "test-contact@example.com" in client.headers["User-Agent"]
