"""Tests for the shared HTTP retry, client and User-Agent helpers."""

from __future__ import annotations

import httpx
import pytest
from composer_http import (
    browser_user_agent,
    call_with_retries,
    contact_email,
    get_json,
    get_text,
    new_client,
    user_agent,
)

# ---------------------------------------------------------------------------
# call_with_retries
# ---------------------------------------------------------------------------


def test_returns_result_without_retrying_on_success() -> None:
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        return "ok"

    assert call_with_retries(do, label="test") == "ok"
    assert len(calls) == 1


def test_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("connection refused")
        return "ok"

    assert call_with_retries(do, label="test") == "ok"
    assert len(calls) == 3


def test_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    with pytest.raises(httpx.ConnectError):
        call_with_retries(do, label="test", retries=4)
    assert len(calls) == 4


def test_retry_after_header_overrides_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("composer_http.time.sleep", sleeps.append)
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        if len(calls) == 1:
            request = httpx.Request("GET", "https://example.com/")
            response = httpx.Response(429, headers={"Retry-After": "30"}, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)
        return "ok"

    assert call_with_retries(do, label="test") == "ok"
    assert 30 in sleeps  # Retry-After overrides the 2^1=2 exponential backoff


def test_backoff_wins_over_smaller_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("composer_http.time.sleep", sleeps.append)
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        if len(calls) == 1:
            request = httpx.Request("GET", "https://example.com/")
            response = httpx.Response(429, headers={"Retry-After": "1"}, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)
        return "ok"

    call_with_retries(do, label="test")
    assert sleeps == [2]  # max(2**1, 1)


def test_unlisted_exceptions_are_not_retried() -> None:
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        raise ValueError("not retryable by default")

    with pytest.raises(ValueError):
        call_with_retries(do, label="test")
    assert len(calls) == 1


def test_retry_on_extends_the_retryable_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("bad json")
        return "ok"

    result = call_with_retries(do, label="test", retry_on=(httpx.HTTPError, ValueError))
    assert result == "ok"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# contact email / User-Agent
# ---------------------------------------------------------------------------


def test_contact_email_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "scraper_contact_email", "someone@example.org")
    assert contact_email() == "someone@example.org"
    assert "someone@example.org" in user_agent()
    assert "someone@example.org" in browser_user_agent()


def test_contact_email_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "scraper_contact_email", None)
    with pytest.raises(RuntimeError, match="SCRAPER_CONTACT_EMAIL"):
        contact_email()


def test_empty_contact_email_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "scraper_contact_email", "")
    with pytest.raises(RuntimeError, match="SCRAPER_CONTACT_EMAIL"):
        user_agent()


# ---------------------------------------------------------------------------
# new_client
# ---------------------------------------------------------------------------


def test_new_client_advertises_the_contact_user_agent() -> None:
    with new_client() as client:
        assert client.headers["User-Agent"] == user_agent()
        assert "test-contact@example.com" in client.headers["User-Agent"]


def test_new_client_merges_extra_headers() -> None:
    with new_client(headers={"Accept-Language": "en"}) as client:
        assert client.headers["Accept-Language"] == "en"
        assert "test-contact@example.com" in client.headers["User-Agent"]


def test_new_client_honours_the_timeout() -> None:
    with new_client(timeout=90) as client:
        assert client.timeout.read == 90


def test_new_client_requires_a_contact_email(monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "scraper_contact_email", None)
    with pytest.raises(RuntimeError, match="SCRAPER_CONTACT_EMAIL"):
        new_client()


# ---------------------------------------------------------------------------
# get_text / get_json
# ---------------------------------------------------------------------------


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # pyright: ignore[reportArgumentType]


def test_get_text_returns_the_body() -> None:
    with _client(lambda _: httpx.Response(200, text="hello")) as client:
        assert get_text(client, "https://example.com/", label="page") == "hello"


def test_get_json_returns_the_payload() -> None:
    with _client(lambda _: httpx.Response(200, json={"a": 1})) as client:
        assert get_json(client, "https://example.com/", label="doc") == {"a": 1}


def test_get_text_retries_error_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, text="ok") if len(calls) > 1 else httpx.Response(503)

    with _client(handler) as client:
        assert get_text(client, "https://example.com/", label="page") == "ok"
    assert len(calls) == 2


def test_get_json_retries_an_undecodable_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body truncated in flight arrives as a 200 and only fails at the decode."""
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, text='{"a": 1')  # truncated
        return httpx.Response(200, json={"a": 1})

    with _client(handler) as client:
        assert get_json(client, "https://example.com/", label="doc") == {"a": 1}
    assert len(calls) == 2


def test_get_text_does_not_retry_a_decode_error() -> None:
    """get_text has no decode step, so ValueError stays non-retryable there."""
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise ValueError("not a transport error")

    with _client(handler) as client, pytest.raises(ValueError):
        get_text(client, "https://example.com/", label="page")
    assert len(calls) == 1


def test_get_json_gives_up_after_the_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    with _client(handler) as client, pytest.raises(httpx.HTTPStatusError):
        get_json(client, "https://example.com/", label="doc", retries=2)
    assert len(calls) == 2
