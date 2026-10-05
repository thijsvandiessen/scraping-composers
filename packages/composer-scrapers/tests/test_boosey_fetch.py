"""Tests for the Boosey HTTP fetch layer: listing pagination and the catalogue walk.

Request retries are ``composer_http.get_text``'s job and are tested in that
package; what is Boosey's own is how listings are paged and how the three-hop
walk (index -> composer -> work) bounds and deduplicates itself.
"""

from __future__ import annotations

from typing import Any

import httpx
from composer_http import SourceSession
from composer_http.testing import mock_session
from composer_scrapers.boosey.fetch import (
    BASE_URL,
    composer_index,
    composer_work_links,
    iter_work_pages,
)


def _session(handler: Any) -> SourceSession:
    return mock_session(handler, follow_redirects=True)


# A two-page composer index over two composers who share one work.
PAGES: dict[str, str] = {
    "/composers": """
        <a href="/composer/A">A</a>
        <a rel="next" href="/composers?page=2">Next</a>
    """,
    "/composers?page=2": '<a href="/composer/B">B</a>',
    "/composer/A": """
        <a href="/cr/music/a-one/1">One</a>
        <a rel="next" href="/composer/A?page=2">Next</a>
    """,
    "/composer/A?page=2": '<a href="/cr/music/a-two/2">Two</a>',
    "/composer/B": '<a href="/cr/music/b-two/2">Two again</a><a href="/cr/music/b-three/3">Three</a>',
}


def _handler(request: httpx.Request) -> httpx.Response:
    key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
    if key in PAGES:
        return httpx.Response(200, text=PAGES[key])
    if key.startswith("/cr/music/"):
        return httpx.Response(200, text=f"<h1>Work {key.rsplit('/', 1)[1]}</h1>")
    return httpx.Response(404, text="not found")


# ---------------------------------------------------------------------------
# listing pagination
# ---------------------------------------------------------------------------


def test_composer_index_follows_rel_next() -> None:
    with _session(_handler) as client:
        assert composer_index(client) == ["/composer/A", "/composer/B"]


def test_composer_work_links_span_paginated_pages() -> None:
    with _session(_handler) as client:
        links = composer_work_links(client, "/composer/A")
    assert [link.work_id for link in links] == ["1", "2"]


def test_listing_stops_when_next_points_at_itself() -> None:
    """A self-referential "next" link would otherwise loop until MAX_LIST_PAGES."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text='<a href="/composer/A">A</a><a rel="next" href="/composers">x</a>')

    with _session(handler) as client:
        assert composer_index(client) == ["/composer/A"]
    assert len(requests) == 1


# ---------------------------------------------------------------------------
# iter_work_pages
# ---------------------------------------------------------------------------


def test_iter_work_pages_walks_composers_then_works() -> None:
    pages = list(iter_work_pages(_session(_handler)))
    assert [link.work_id for link, _, _ in pages] == ["1", "2", "3"]
    assert pages[0][1] == BASE_URL + "/cr/music/a-one/1"
    assert "<h1>Work 1</h1>" in pages[0][2]


def test_iter_work_pages_fetches_a_shared_work_once() -> None:
    """Work 2 is listed under both composers; it must not be fetched twice."""
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/cr/music/"):
            fetched.append(request.url.path)
        return _handler(request)

    list(iter_work_pages(_session(handler)))
    assert len(fetched) == len(set(fetched)) == 3


def test_iter_work_pages_honours_max_pages() -> None:
    pages = list(iter_work_pages(_session(_handler), max_pages=2))
    assert [link.work_id for link, _, _ in pages] == ["1", "2"]
