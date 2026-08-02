"""Tests for the Boosey HTTP fetch layer: retries, pagination and the walk."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers.boosey.fetch import (
    BASE_URL,
    _get_text,
    _make_client,
    composer_index,
    composer_work_links,
    iter_work_pages,
)


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    class _MockedClient(httpx.Client):
        def __init__(self, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    monkeypatch.setattr("composer_scrapers.boosey.fetch.httpx.Client", _MockedClient)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness delays are real seconds; drop them for the suite."""
    monkeypatch.setattr("composer_scrapers.boosey.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)


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
# _get_text
# ---------------------------------------------------------------------------


def test_get_text_returns_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda request: httpx.Response(200, text="hello"))
    with _make_client() as client:
        assert _get_text(client, BASE_URL + "/composers", "test") == "hello"


def test_get_text_retries_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503 if len(attempts) < 3 else 200, text="ok")

    _patch_client(monkeypatch, handler)
    with _make_client() as client:
        assert _get_text(client, BASE_URL + "/composers", "test") == "ok"
    assert len(attempts) == 3


def test_get_text_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda request: httpx.Response(503, text="down"))
    with pytest.raises(httpx.HTTPStatusError):
        with _make_client() as client:
            _get_text(client, BASE_URL + "/composers", "test")


# ---------------------------------------------------------------------------
# listing pagination
# ---------------------------------------------------------------------------


def test_composer_index_follows_rel_next(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _handler)
    with _make_client() as client:
        assert composer_index(client) == ["/composer/A", "/composer/B"]


def test_composer_work_links_span_paginated_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _handler)
    with _make_client() as client:
        links = composer_work_links(client, "/composer/A")
    assert [link.work_id for link in links] == ["1", "2"]


def test_listing_stops_when_next_points_at_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """A self-referential "next" link would otherwise loop until MAX_LIST_PAGES."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text='<a href="/composer/A">A</a><a rel="next" href="/composers">x</a>')

    _patch_client(monkeypatch, handler)
    with _make_client() as client:
        assert composer_index(client) == ["/composer/A"]
    assert len(requests) == 1


# ---------------------------------------------------------------------------
# iter_work_pages
# ---------------------------------------------------------------------------


def test_iter_work_pages_walks_composers_then_works(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _handler)
    pages = list(iter_work_pages())
    assert [link.work_id for link, _, _ in pages] == ["1", "2", "3"]
    assert pages[0][1] == BASE_URL + "/cr/music/a-one/1"
    assert "<h1>Work 1</h1>" in pages[0][2]


def test_iter_work_pages_fetches_a_shared_work_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Work 2 is listed under both composers; it must not be fetched twice."""
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/cr/music/"):
            fetched.append(request.url.path)
        return _handler(request)

    _patch_client(monkeypatch, handler)
    list(iter_work_pages())
    assert len(fetched) == len(set(fetched)) == 3


def test_iter_work_pages_honours_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _handler)
    pages = list(iter_work_pages(max_pages=2))
    assert [link.work_id for link, _, _ in pages] == ["1", "2"]
