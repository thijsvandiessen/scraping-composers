"""Tests for the imslp_recordings HTTP layer: the category listing's
continuation, the per-page parse call, and what a failing page costs.

The response shapes below mirror what api.php answers live. Two of them are
MediaWiki 1.18 specifics worth pinning, because a newer wiki answers
differently and the difference is silent: continuation arrives under
``query-continue`` rather than ``continue``, and an unknown page comes back as
a 200 carrying ``{"error": ...}`` rather than a 404.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers.imslp_recordings.fetch import (
    BASE_URL,
    iter_category_members,
    iter_recording_pages,
    page_url,
    parse_url,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness delays are real seconds; drop them for the suite."""
    monkeypatch.setattr("composer_scrapers.imslp_recordings.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _members(members: list[tuple[int, str]], cmcontinue: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": {"categorymembers": [{"pageid": pageid, "title": title} for pageid, title in members]}
    }
    if cmcontinue is not None:
        body["query-continue"] = {"categorymembers": {"cmcontinue": cmcontinue}}
    return body


def _parsed(title: str, document: str) -> dict[str, Any]:
    return {"parse": {"title": title, "text": {"*": document}}}


def _page(album_id: int) -> str:
    return f'<script>JGCommRec=[{{"aid":{album_id},"tit":"An Album","art":"Someone (piano)"}}];</script>'


# Two listing pages and the four work pages they name, keyed the way the
# handlers below look them up: by the API parameters, not the path, since every
# request in this source goes to the same /api.php.
LISTING = {
    None: _members([(1, "First (A, B)"), (2, "Second (C, D)")], cmcontinue="page|SECOND"),
    "page|SECOND": _members([(3, "Third (E, F)")]),
}


def _handler(pages: dict[int, dict[str, Any]] | None = None) -> Any:
    documents = pages if pages is not None else {1: _parsed("First (A, B)", _page(11))}

    def handle(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("action") == "query":
            return httpx.Response(200, json=LISTING[params.get("cmcontinue")])
        pageid = int(params["pageid"])
        if pageid not in documents:
            return httpx.Response(500)
        return httpx.Response(200, json=documents[pageid])

    return handle


class TestUrls:
    def test_a_page_is_parsed_by_id_not_by_title(self) -> None:
        """Titles in this category are a minefield to encode; ids are not."""
        assert parse_url(207061) == f"{BASE_URL}/api.php?action=parse&pageid=207061&prop=text&format=json"

    def test_a_permalink_points_at_the_work_page_not_the_api(self) -> None:
        assert page_url("'O sole mio (Di Capua, Eduardo)") == (
            f"{BASE_URL}/wiki/'O_sole_mio_(Di_Capua,_Eduardo)"
        )

    def test_a_permalink_encodes_what_a_url_cannot_carry(self) -> None:
        assert page_url("1.X.1905 (Janáček, Leoš)").startswith(f"{BASE_URL}/wiki/1.X.1905_(Jan")
        assert " " not in page_url("1.X.1905 (Janáček, Leoš)")


class TestCategoryMembers:
    def test_follows_the_continuation_to_the_end(self) -> None:
        with _client(_handler()) as client:
            assert list(iter_category_members(client)) == [
                (1, "First (A, B)"),
                (2, "Second (C, D)"),
                (3, "Third (E, F)"),
            ]

    def test_stops_when_no_continuation_comes_back(self) -> None:
        """A 1.18 wiki omits query-continue entirely on the last page."""

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_members([(1, "Only (A, B)")]))

        with _client(handle) as client:
            assert list(iter_category_members(client)) == [(1, "Only (A, B)")]

    def test_a_member_missing_its_id_is_skipped(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            body = {"query": {"categorymembers": [{"title": "No id"}, {"pageid": 5, "title": "Kept"}]}}
            return httpx.Response(200, json=body)

        with _client(handle) as client:
            assert list(iter_category_members(client)) == [(5, "Kept")]


class TestRecordingPages:
    def test_yields_the_parser_output_and_the_title_the_api_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The API names the page back, so the listing's title is not trusted."""
        pages = {1: _parsed("First, Renamed (A, B)", _page(11))}
        _use(monkeypatch, _handler(pages))
        (page,) = list(iter_recording_pages(max_pages=1))
        assert page[0] == 1
        assert page[1] == "First, Renamed (A, B)"
        assert page[2] == f"{BASE_URL}/wiki/First,_Renamed_(A,_B)"
        assert "JGCommRec" in page[3]

    def test_max_pages_caps_the_parse_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pages = {n: _parsed(f"Page {n}", _page(n)) for n in (1, 2, 3)}
        _use(monkeypatch, _handler(pages))
        assert len(list(iter_recording_pages(max_pages=2))) == 2

    def test_reads_every_page_when_uncapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pages = {n: _parsed(f"Page {n}", _page(n)) for n in (1, 2, 3)}
        _use(monkeypatch, _handler(pages))
        assert [page[0] for page in iter_recording_pages()] == [1, 2, 3]

    def test_a_page_that_fails_does_not_end_the_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """26,933 pages is hours of fetching; one bad page must not cost it."""
        pages = {1: _parsed("Page 1", _page(11)), 3: _parsed("Page 3", _page(33))}
        _use(monkeypatch, _handler(pages))
        assert [page[0] for page in iter_recording_pages()] == [1, 3]

    def test_an_api_error_body_is_skipped_like_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """1.18 answers an unknown page id with a 200 and an error object."""
        pages: dict[int, dict[str, Any]] = {
            1: {"error": {"code": "nosuchpageid", "info": "There is no page with ID 1"}},
            2: _parsed("Page 2", _page(22)),
            3: _parsed("Page 3", _page(33)),
        }
        _use(monkeypatch, _handler(pages))
        assert [page[0] for page in iter_recording_pages()] == [2, 3]

    def test_an_unreadable_body_is_skipped_like_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            if params.get("action") == "query":
                return httpx.Response(200, json=LISTING[params.get("cmcontinue")])
            if params["pageid"] == "2":
                return httpx.Response(200, text="<html>not json at all</html>")
            return httpx.Response(200, json=_parsed("A page", _page(1)))

        _use(monkeypatch, handle)
        assert [page[0] for page in iter_recording_pages()] == [1, 3]


class TestPageMirror:
    def test_a_mirrored_page_is_not_fetched_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def handle(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            if params.get("action") == "query":
                return httpx.Response(200, json=_members([(1, "Only (A, B)")]))
            calls.append(int(params["pageid"]))
            return httpx.Response(200, json=_parsed("Only (A, B)", _page(11)))

        _use(monkeypatch, handle)
        cache = _FakeCache()
        monkeypatch.setattr("composer_scrapers.imslp_recordings.fetch.open_page_cache", lambda: cache)
        assert len(list(iter_recording_pages())) == 1
        assert len(list(iter_recording_pages())) == 1
        assert calls == [1]

    def test_a_page_that_failed_is_not_mirrored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Storing an error would mean never retrying the page that produced it."""

        def handle(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            if params.get("action") == "query":
                return httpx.Response(200, json=_members([(1, "Only (A, B)")]))
            return httpx.Response(200, json={"error": {"code": "nosuchpageid"}})

        _use(monkeypatch, handle)
        cache = _FakeCache()
        monkeypatch.setattr("composer_scrapers.imslp_recordings.fetch.open_page_cache", lambda: cache)
        assert list(iter_recording_pages()) == []
        assert cache.stored == {}


class _FakeCache:
    """Enough of PageCache to see what a sweep would have mirrored."""

    def __init__(self) -> None:
        self.stored: dict[str, str] = {}

    def get(self, url: str) -> str | None:
        return self.stored.get(url)

    def put(self, url: str, body: str) -> None:
        self.stored[url] = body

    def summary(self) -> str:
        return f"{len(self.stored)} stored"


def _use(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Point the source's client at *handler*, and its mirror at nothing."""
    monkeypatch.setattr(
        "composer_scrapers.imslp_recordings.fetch.new_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr("composer_scrapers.imslp_recordings.fetch.open_page_cache", lambda: None)
