"""Tests for the source session: politeness, the mirror rules, tolerance.

What every adapter used to state in its own docstring and re-implement in its
own fetch module is asserted here once.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from composer_http import PageCache, SourceSession, request_fingerprint
from composer_http.testing import mock_session

URL = "https://example.org/concert/1/"


class _Site:
    """A handler that counts what it was asked and answers from a dict."""

    def __init__(self, pages: dict[str, httpx.Response] | None = None) -> None:
        self.pages = pages or {}
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(f"{request.method} {request.url}")
        return self.pages.get(str(request.url), httpx.Response(200, text=f"page {len(self.asked)}"))


@pytest.fixture
def cache(tmp_path: Path) -> PageCache:
    return PageCache(tmp_path / "pages.db")


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr("composer_http.session.time.sleep", calls.append)
    monkeypatch.setattr("composer_http.time.sleep", calls.append)
    return calls


def _polite(site: _Site, cache: PageCache | None = None, delay_s: float = 10.0) -> SourceSession:
    client = httpx.Client(transport=httpx.MockTransport(site))
    return SourceSession("test", client, delay_s=delay_s, cache=cache)


# ---- politeness ---- #


def test_the_first_request_does_not_wait(slept: list[float]) -> None:
    _polite(_Site()).get_text(URL, label="x")
    assert slept == []


def test_consecutive_requests_are_spaced_by_the_delay(slept: list[float]) -> None:
    session = _polite(_Site())
    session.get_text(URL, label="x")
    session.get_text(URL, label="x")
    assert len(slept) == 1
    assert 9.0 < slept[0] <= 10.0


def test_a_mirror_hit_costs_no_pause(slept: list[float], cache: PageCache) -> None:
    session = _polite(_Site(), cache=cache)
    session.get_text(URL, label="x", mirror=True)
    session.get_text(URL, label="x", mirror=True)
    session.get_text(URL, label="x", mirror=True)
    assert slept == []
    assert session.requests == 1


# ---- the mirror ---- #


def test_a_mirrored_page_is_served_without_a_request(cache: PageCache) -> None:
    site = _Site()
    session = mock_session(site, cache=cache)
    first = session.get_text(URL, label="x", mirror=True)
    assert session.get_text(URL, label="x", mirror=True) == first
    assert len(site.asked) == 1


def test_an_unmirrored_request_always_goes_out(cache: PageCache) -> None:
    """Enumerators (indexes, listings, sitemaps) are how a re-run sees growth."""
    site = _Site()
    session = mock_session(site, cache=cache)
    session.get_text(URL, label="x")
    session.get_text(URL, label="x")
    assert len(site.asked) == 2
    assert cache.get(URL) is None


def test_a_page_the_validator_rejects_is_returned_but_not_mirrored(cache: PageCache) -> None:
    site = _Site({URL: httpx.Response(200, text="<html>bot check</html>")})
    session = mock_session(site, cache=cache)
    assert session.get_text(URL, label="x", mirror=True, valid=lambda body: "concert" in body) == (
        "<html>bot check</html>"
    )
    session.get_text(URL, label="x", mirror=True, valid=lambda body: "concert" in body)
    assert len(site.asked) == 2


def test_a_mirrored_page_expires_with_the_sessions_max_age(cache: PageCache) -> None:
    site = _Site()
    mock_session(site, cache=cache).get_text(URL, label="x", mirror=True)
    # a cadence so short every page is already stale
    stale = mock_session(site, cache=cache, max_age=timedelta(0))
    stale.get_text(URL, label="x", mirror=True)
    assert len(site.asked) == 2


def test_a_session_without_a_max_age_keeps_its_mirror(cache: PageCache) -> None:
    site = _Site()
    mock_session(site, cache=cache).get_text(URL, label="x", mirror=True)
    mock_session(site, cache=cache, max_age=None).get_text(URL, label="x", mirror=True)
    assert len(site.asked) == 1


def test_a_mirrored_post_is_refetched_when_its_query_changes(cache: PageCache) -> None:
    site = _Site()
    session = mock_session(site, cache=cache)

    def ask(query: str) -> str:
        return session.mirrored(
            "graphql://test/1",
            lambda: session.post_text(URL, label="q", json={"query": query}),
            fingerprint=request_fingerprint("POST", URL, query),
        )

    assert ask("{ a }") == ask("{ a }")
    ask("{ a b }")
    assert site.asked == [f"POST {URL}", f"POST {URL}"]


def test_store_and_lookup_share_the_mirror_rules(cache: PageCache) -> None:
    session = mock_session(_Site(), cache=cache)
    session.store("k", "v", fingerprint="f")
    assert session.lookup("k", fingerprint="f") == "v"
    assert session.lookup("k", fingerprint="g") is None


def test_without_a_mirror_lookup_finds_nothing() -> None:
    session = mock_session(_Site())
    session.store("k", "v")
    assert session.lookup("k") is None


# ---- tolerance and retries ---- #


def test_try_get_text_answers_none_for_a_page_that_cannot_be_fetched(
    slept: list[float], cache: PageCache
) -> None:
    site = _Site({URL: httpx.Response(404)})
    session = mock_session(site, cache=cache)
    assert session.try_get_text(URL, label="x", mirror=True) is None
    assert cache.get(URL) is None  # a failure is never mirrored


def test_get_text_raises_once_retries_are_spent(slept: list[float]) -> None:
    site = _Site({URL: httpx.Response(503)})
    with pytest.raises(httpx.HTTPStatusError):
        mock_session(site).get_text(URL, label="x", retries=2)
    assert len(site.asked) == 2


def test_get_json_retries_a_truncated_body(slept: list[float]) -> None:
    answers = iter([httpx.Response(200, text='{"a": '), httpx.Response(200, json={"a": 1})])
    session = mock_session(lambda _request: next(answers))
    assert session.get_json(URL, label="x") == {"a": 1}


def test_get_json_mirrors_only_an_object(cache: PageCache) -> None:
    session = mock_session(_Site({URL: httpx.Response(200, json={"a": 1})}), cache=cache)
    assert session.get_json(URL, label="x", mirror=True) == {"a": 1}
    assert json.loads(cache.get(URL) or "null") == {"a": 1}


def test_a_corrupt_json_mirror_entry_is_refetched(cache: PageCache) -> None:
    cache.put(URL, '{"a": ')
    site = _Site({URL: httpx.Response(200, json={"a": 2})})
    assert mock_session(site, cache=cache).get_json(URL, label="x", mirror=True) == {"a": 2}
    assert len(site.asked) == 1


def test_the_summary_counts_requests_and_the_mirror(cache: PageCache) -> None:
    session = mock_session(_Site(), cache=cache)
    session.get_text(URL, label="x", mirror=True)
    session.get_text(URL, label="x", mirror=True)
    assert session.summary() == "1 requests; page mirror: 1 mirrored, 1 fetched (50% of requests saved)"


def test_leaving_the_session_closes_its_client() -> None:
    with mock_session(_Site()) as session:
        pass
    assert session.client.is_closed
