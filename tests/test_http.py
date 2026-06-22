"""Tests for the shared Http helper (retries, backoff, Retry-After, parsing)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from composer_ingest.http import Http


def _http(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> Http:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Http(client, **kwargs)  # type: ignore[arg-type]


def test_get_json_returns_parsed_body() -> None:
    http = _http(lambda r: httpx.Response(200, json={"ok": True}))
    assert http.get_json("https://x/") == {"ok": True}


def test_get_text_returns_body_text() -> None:
    http = _http(lambda r: httpx.Response(200, text="hello"))
    assert http.get_text("https://x/") == "hello"


def test_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.http.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True})

    http = _http(handler, retries=3)
    assert http.get_json("https://x/") == {"ok": True}
    assert len(attempts) == 3


def test_raises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.http.time.sleep", lambda _: None)
    http = _http(lambda r: httpx.Response(500, text="boom"), retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        http.get_json("https://x/")


def test_malformed_json_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.http.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(200, text="{not json")
        return httpx.Response(200, json={"ok": True})

    http = _http(handler, retries=3)
    assert http.get_json("https://x/") == {"ok": True}
    assert len(attempts) == 2


def test_honor_retry_after_overrides_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("composer_ingest.http.time.sleep", sleeps.append)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "30"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    http = _http(handler, retries=3, honor_retry_after=True)
    assert http.get_json("https://x/") == {"ok": True}
    assert 30 in sleeps  # Retry-After overrides the 2^1=2s backoff


def test_post_json_extract_keyerror_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.http.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(200, json={"unexpected": True})  # missing "data"
        return httpx.Response(200, json={"data": [1, 2]})

    http = _http(handler, retries=3)
    result = http.post_json("https://x/", extract=lambda payload: payload["data"])
    assert result == [1, 2]
    assert len(attempts) == 2
