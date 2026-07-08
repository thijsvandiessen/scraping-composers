"""Tests for the RCO HTTP fetch layer."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers.rco.fetch import (
    BASE_URL,
    _get_text,
    _make_client,
    page_slugs,
)


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    class _MockedClient(httpx.Client):
        def __init__(self, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    monkeypatch.setattr("composer_scrapers.rco.fetch.httpx.Client", _MockedClient)


# ---------------------------------------------------------------------------
# page_slugs
# ---------------------------------------------------------------------------


def test_page_slugs_extracts_concert_slugs() -> None:
    html = """
    <a href="/en/calendar/beethoven-symphony-5-2026-09-01/">Concert</a>
    <a href="/en/calendar/brahms-violin-concerto-2026-10-15/">Concert</a>
    """
    assert page_slugs(html) == [
        "beethoven-symphony-5-2026-09-01",
        "brahms-violin-concerto-2026-10-15",
    ]


def test_page_slugs_ignores_calendar_root_and_other_links() -> None:
    html = """
    <a href="/en/calendar/">All concerts</a>
    <a href="/en/orchestra/">Orchestra</a>
    <a href="/en/calendar/beethoven-2026-09-01/">Concert</a>
    """
    assert page_slugs(html) == ["beethoven-2026-09-01"]


def test_page_slugs_requires_date_suffix() -> None:
    html = '<a href="/en/calendar/no-date-here/">Not a concert slug</a>'
    assert page_slugs(html) == []


# ---------------------------------------------------------------------------
# _get_text
# ---------------------------------------------------------------------------


def test_get_text_returns_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello")

    _patch_client(monkeypatch, handler)
    with _make_client() as client:
        assert _get_text(client, BASE_URL + "/en/calendar/", "test") == "hello"


def test_get_text_retries_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="Error")
        return httpx.Response(200, text="ok")

    _patch_client(monkeypatch, handler)
    with _make_client() as client:
        result = _get_text(client, BASE_URL + "/en/", "test")
    assert result == "ok"
    assert len(attempts) == 3


def test_get_text_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Always failing")

    _patch_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        with _make_client() as client:
            _get_text(client, BASE_URL + "/en/", "test")
