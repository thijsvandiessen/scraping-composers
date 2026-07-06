"""Tests for the Concertgebouw HTTP fetch layer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from composer_ingest.scraper.sources.concertgebouw.fetch import (
    SEARCH_URL,
    _fetch,
    _fetch_list_page,
    _fetch_search_page,
)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Replace httpx.Client inside concertgebouw.fetch with one backed by a mock transport."""

    class _MockedClient(httpx.Client):
        def __init__(self, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    monkeypatch.setattr("composer_ingest.scraper.sources.concertgebouw.fetch.httpx.Client", _MockedClient)


# ---------------------------------------------------------------------------
# _fetch
# ---------------------------------------------------------------------------


def test_fetch_returns_response_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>archive</html>")

    _patch_client(monkeypatch, handler)
    assert _fetch("test", method="GET", url=SEARCH_URL) == "<html>archive</html>"


def test_fetch_retries_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources.concertgebouw.fetch.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="Server Error")
        return httpx.Response(200, text="<html>ok</html>")

    _patch_client(monkeypatch, handler)
    result = _fetch("test", method="GET", url=SEARCH_URL)

    assert len(attempts) == 3
    assert result == "<html>ok</html>"


def test_fetch_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources.concertgebouw.fetch.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Always failing")

    _patch_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        _fetch("test", method="GET", url=SEARCH_URL)


# ---------------------------------------------------------------------------
# _fetch_search_page and _fetch_list_page
# ---------------------------------------------------------------------------


def test_fetch_search_page_issues_get_to_search_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_fetch(label: str, **kwargs: Any) -> str:
        calls.append(kwargs)
        return "<html/>"

    monkeypatch.setattr("composer_ingest.scraper.sources.concertgebouw.fetch._fetch", fake_fetch)
    _fetch_search_page()

    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == SEARCH_URL


def test_fetch_list_page_issues_post_with_list_button(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_fetch(label: str, **kwargs: Any) -> str:
        calls.append(kwargs)
        return "<html/>"

    monkeypatch.setattr("composer_ingest.scraper.sources.concertgebouw.fetch._fetch", fake_fetch)
    _fetch_list_page()

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == SEARCH_URL
    assert calls[0]["files"] == {"list": (None, "List")}
