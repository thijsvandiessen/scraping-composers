"""Tests for the classicfm HTTP fetch layer.

Both index pages are single documents with no pagination, so what matters
here is simply that the right two URLs are requested, in order, with a
politeness delay between them. Retries and the delay itself are the
session's job and are tested in ``composer_http``.
"""

from __future__ import annotations

import httpx
from composer_http.testing import mock_session
from composer_scrapers.classicfm import ClassicFmAdapter
from composer_scrapers.classicfm.fetch import ARTISTS_URL, COMPOSERS_URL, fetch_index_pages

PAGES: dict[str, str] = {
    "/composers/": "<html>composers index</html>",
    "/artists/": "<html>artists index</html>",
}


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path in PAGES:
        return httpx.Response(200, text=PAGES[request.url.path])
    return httpx.Response(404, text="not found")


def test_fetch_index_pages_returns_both_pages() -> None:
    composers_html, artists_html = fetch_index_pages(mock_session(_handler))
    assert composers_html == "<html>composers index</html>"
    assert artists_html == "<html>artists index</html>"


def test_fetch_index_pages_requests_composers_then_artists() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return _handler(request)

    fetch_index_pages(mock_session(handler))
    assert requested == ["/composers/", "/artists/"]


def test_adapter_session_is_polite_and_identified() -> None:
    with ClassicFmAdapter().open_session() as session:
        assert session.delay_s == 0.5
        assert session.client.headers["User-Agent"]
        assert session.client.follow_redirects


def test_urls_target_classicfm() -> None:
    assert COMPOSERS_URL == "https://www.classicfm.com/composers/"
    assert ARTISTS_URL == "https://www.classicfm.com/artists/"
