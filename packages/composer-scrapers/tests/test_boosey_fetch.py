"""Tests for the Boosey HTTP fetch layer: the index, session-held searches and the walk.

Request retries are ``composer_http.get_text``'s job and are tested in that
package; what is Boosey's own is how a search is paged and how the three-hop
walk (index -> composer search -> work) bounds and deduplicates itself.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_http import SourceSession
from composer_http.testing import mock_session
from composer_scrapers.boosey.fetch import (
    BASE_URL,
    composer_work_links,
    iter_composer_ids,
    iter_work_pages,
)

SEARCH = "/cr/catalogue/ps/powersearch_results"
ITEMS = "/cr/catalogue/ps/ps_items.cshtml"

#: Letter A links composer 1 to their search and composer 2 to their own page,
#: which features one work but links their search. Composer 2 is also listed
#: under B. The two share work 2.
INDEX = {
    "A": f"""
        <a href="/pages/cr/composer/?l1=B">B</a>
        <a href="{SEARCH}?composerid=1">Search only</a>
        <a href="/composer/Two">Has a page</a>
    """,
    "B": '<a href="/composer/Two">Has a page</a>',
}
COMPOSER_PAGE = f"""
    <a href="/cr/music/featured/3">Featured</a>
    <a href="{SEARCH}?composerid=2&DL_ClassificationGroupIDs=14066">Opera</a>
"""
#: Each composer's search, page by page.
RESULTS = {
    "1": {
        None: f"""
            <a href="/cr/music/one/1">One</a>
            <a href="{ITEMS}?pg=2&amp;type=nf" class="index__letter pageNext"><span>&gt;&gt;</span></a>
        """,
        "2": '<a href="/cr/music/two/2">Two</a>',
    },
    "2": {None: '<a href="/cr/music/two/2">Two again</a><a href="/cr/music/three/3">Three</a>'},
}


def _site() -> Any:
    """A handler over the site above, holding the current search per session as the real one does."""
    search: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path == SEARCH:
            search["composer"] = params["composerid"]
            return httpx.Response(200, text="requires JavaScript", headers={"Set-Cookie": "sid=abc"})
        if path == ITEMS and "sid=abc" not in request.headers.get("Cookie", ""):
            return httpx.Response(399)
        if path == ITEMS:
            body = RESULTS[search["composer"]][params.get("pg")]
        elif path == "/pages/cr/composer/":
            body = INDEX.get(params["l1"], "")
        elif path == "/composer/Two":
            body = COMPOSER_PAGE
        elif path.startswith("/cr/music/"):
            body = f"<h1>Work {path.rsplit('/', 1)[1]}</h1>"
        else:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=body)

    return handler


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _session(handler: Any) -> SourceSession:
    return mock_session(handler, follow_redirects=True)


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------


def test_composer_ids_come_from_the_index_and_from_composer_pages() -> None:
    with _session(_site()) as client:
        assert list(iter_composer_ids(client)) == ["1", "2"]


def test_an_empty_index_fails_the_run() -> None:
    """A redesign must fail the run, not complete it with zero records."""
    with _session(lambda _: httpx.Response(200, text="<html>redesigned</html>")) as client:
        with pytest.raises(RuntimeError, match="lists no composers"):
            list(iter_composer_ids(client))


def test_a_composer_page_with_no_id_costs_only_that_composer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/composer/Two":
            return httpx.Response(404)
        return _site()(request)

    with _session(handler) as client:
        assert list(iter_composer_ids(client)) == ["1"]


# ---------------------------------------------------------------------------
# searches
# ---------------------------------------------------------------------------


def test_composer_work_links_span_the_pages_of_a_search() -> None:
    with _session(_site()) as client:
        links = composer_work_links(client, "1")
    assert [link.work_id for link in links] == ["1", "2"]


def test_each_search_starts_in_the_session_before_its_items_are_read() -> None:
    """The items URL names no composer, so a search not started first serves the last one's."""
    with _session(_site()) as client:
        composer_work_links(client, "1")
        links = composer_work_links(client, "2")
    assert [link.work_id for link in links] == ["2", "3"]


def test_a_search_stops_when_next_points_at_itself() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, text=f'<a href="{ITEMS}" class="pageNext">x</a>')

    with _session(handler) as client:
        assert composer_work_links(client, "1") == []
    assert requests == [SEARCH, ITEMS]


# ---------------------------------------------------------------------------
# iter_work_pages
# ---------------------------------------------------------------------------


def test_iter_work_pages_walks_composers_then_works() -> None:
    pages = list(iter_work_pages(_session(_site())))
    assert [link.work_id for link, _, _ in pages] == ["1", "2", "3"]
    assert pages[0][1] == BASE_URL + "/cr/music/one/1"
    assert "<h1>Work 1</h1>" in pages[0][2]


def test_iter_work_pages_fetches_a_shared_work_once() -> None:
    """Work 2 is listed under both composers; it must not be fetched twice."""
    fetched: list[str] = []
    site = _site()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/cr/music/"):
            fetched.append(request.url.path)
        return site(request)

    list(iter_work_pages(_session(handler)))
    assert len(fetched) == len(set(fetched)) == 3


def test_iter_work_pages_honours_max_pages() -> None:
    pages = list(iter_work_pages(_session(_site()), max_pages=2))
    assert [link.work_id for link, _, _ in pages] == ["1", "2"]


def test_a_work_page_that_fails_costs_only_that_work() -> None:
    site = _site()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cr/music/two/2":
            return httpx.Response(404)
        return site(request)

    pages = list(iter_work_pages(_session(handler)))
    assert [link.work_id for link, _, _ in pages] == ["1", "3"]
