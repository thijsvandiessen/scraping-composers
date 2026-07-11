"""Tests for the shared HTTP retry and User-Agent helper."""

from __future__ import annotations

import httpx
import pytest
from composer_scrapers._http import browser_user_agent, call_with_retries, contact_email, user_agent

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
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("connection refused")
        return "ok"

    assert call_with_retries(do, label="test") == "ok"
    assert len(calls) == 3


def test_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)
    calls: list[int] = []

    def do() -> str:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    with pytest.raises(httpx.ConnectError):
        call_with_retries(do, label="test", retries=4)
    assert len(calls) == 4


def test_retry_after_header_overrides_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("composer_scrapers._http.time.sleep", sleeps.append)
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
    monkeypatch.setattr("composer_scrapers._http.time.sleep", sleeps.append)
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
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)
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
