"""Tests for the Concertgebouw HTTP fetch layer.

Retries are the session's job and are tested in ``composer_http``; what is the
Concertgebouw's own is which two requests make up the source.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from composer_http import PageCache
from composer_http.testing import mock_session
from composer_scrapers.concertgebouw import ConcertgebouwAdapter
from composer_scrapers.concertgebouw.fetch import SEARCH_URL, _fetch_list_page, _fetch_search_page


def test_fetch_search_page_issues_get_to_search_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="<html>archive</html>")

    assert _fetch_search_page(mock_session(handler)) == "<html>archive</html>"
    assert [(r.method, str(r.url)) for r in requests] == [("GET", SEARCH_URL)]


def test_fetch_list_page_issues_post_with_list_button() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="<html/>")

    _fetch_list_page(mock_session(handler))

    assert [(r.method, str(r.url)) for r in requests] == [("POST", SEARCH_URL)]
    body = requests[0].read()
    assert b'name="list"' in body
    assert b"List" in body


def test_neither_view_is_mirrored(tmp_path: Path) -> None:
    """Both views are the whole archive: a re-run must see what was added."""
    cache = PageCache(tmp_path / "pages.db")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="<html/>")

    session = mock_session(handler, cache=cache)
    for _ in range(2):
        _fetch_search_page(session)
        _fetch_list_page(session)
    assert len(requests) == 4


def test_adapter_shares_one_session_across_both_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    # the parsers raise unless the search page has every filter select and the
    # list view has the result table, so serve an empty-but-well-formed page
    selects = "".join(
        f'<select id="{select_id}"></select>' for select_id in ("componistcode", "dirigentcode", "solistcode")
    )
    page = f'<html>{selects}<table id="zoekresultaat"></table></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=page)

    session = mock_session(handler)
    monkeypatch.setattr(ConcertgebouwAdapter, "open_session", lambda self: session)
    list(ConcertgebouwAdapter().fetch())

    assert [request.method for request in requests] == ["GET", "POST"]
    assert session.requests == 2
