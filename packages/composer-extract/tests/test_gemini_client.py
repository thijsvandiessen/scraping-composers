"""The Gemini structured-output request/response shape, and the schema translation
that makes Pydantic's JSON Schema usable as Gemini's ``responseSchema``."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from composer_config import settings
from composer_extract import GeminiExtractor, GeminiTuning
from composer_extract.schema import PageExtraction

_LOGGER = "composer_extract.gemini_client"


class RecordingPost:
    """Stands in for the live ``httpx.post`` call, returning a canned
    generateContent response body built from *text*."""

    def __init__(self, text: str, *, finish_reason: str = "STOP", **usage: Any) -> None:
        self._text = text
        self._finish_reason = finish_reason
        self._usage = usage
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return {
            "candidates": [
                {"content": {"parts": [{"text": self._text}]}, "finishReason": self._finish_reason}
            ],
            "usageMetadata": self._usage,
        }


def test_from_settings_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_ai_api_key", None)

    with pytest.raises(RuntimeError, match="GOOGLE_AI_API_KEY"):
        GeminiExtractor.from_settings()


def test_from_settings_uses_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_ai_api_key", "test-key")
    monkeypatch.setattr(settings, "google_ai_model", "gemini-flash-lite-latest")
    post = RecordingPost('{"concerts": []}')

    GeminiExtractor.from_settings(post=post).extract_page("# Page", {})

    assert post.kwargs["model"] == "gemini-flash-lite-latest"


def test_from_settings_wires_explicit_pacing_and_daily_limit_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit GOOGLE_AI_MIN_INTERVAL_S / GOOGLE_AI_MAX_REQUESTS_PER_DAY wins
    over whatever the model's own table entry says — e.g. a paid tier's higher
    ceiling on an otherwise free-tier-limited model."""
    monkeypatch.setattr(settings, "google_ai_api_key", "test-key")
    monkeypatch.setattr(settings, "google_ai_model", "gemini-flash-lite-latest")
    monkeypatch.setattr(settings, "google_ai_min_interval_s", 9.0)
    monkeypatch.setattr(settings, "google_ai_max_requests_per_day", 12345)

    extractor = GeminiExtractor.from_settings(post=RecordingPost("{}"))

    assert extractor._governor._min_interval_s == 9.0
    assert extractor._governor._max_requests_per_day == 12345


@pytest.mark.parametrize(
    ("model", "expected_interval", "expected_daily_limit"),
    [
        ("gemini-flash-lite-latest", 4.0, 500),
        ("gemini-3.1-flash-lite", 4.0, 500),
        ("gemma-3-27b-it", 2.0, 14400),
        ("some-future-model-not-in-the-table", 4.0, 500),
    ],
)
def test_from_settings_derives_pacing_and_daily_limit_from_the_model(
    monkeypatch: pytest.MonkeyPatch, model: str, expected_interval: float, expected_daily_limit: int
) -> None:
    """With no explicit override, pacing and the daily cap come from the
    configured model's own free-tier limits — an unrecognised model falls back
    to gemini-flash-lite-latest's (the more conservative of the two known
    tiers) rather than getting no pacing at all."""
    monkeypatch.setattr(settings, "google_ai_api_key", "test-key")
    monkeypatch.setattr(settings, "google_ai_model", model)
    monkeypatch.setattr(settings, "google_ai_min_interval_s", None)
    monkeypatch.setattr(settings, "google_ai_max_requests_per_day", None)

    extractor = GeminiExtractor.from_settings(post=RecordingPost("{}"))

    assert extractor._governor._min_interval_s == expected_interval
    assert extractor._governor._max_requests_per_day == expected_daily_limit


def test_a_valid_response_parses() -> None:
    extractor = GeminiExtractor(
        model="gemini-flash-lite-latest", api_key="k", post=RecordingPost('{"concerts": []}')
    )

    assert extractor.extract_page("# Page", {}) == PageExtraction()


def test_response_is_validated_not_salvaged() -> None:
    truncated = '{"concerts": [{"date": "2024-05-01", "soloists": [{"name": "X", "discipline": "Piano'
    extractor = GeminiExtractor(model="gemini-flash-lite-latest", api_key="k", post=RecordingPost(truncated))

    with pytest.raises(ValueError):
        extractor.extract_page("# Page", {})


def test_no_candidates_is_a_failure() -> None:
    def post(**kwargs: Any) -> dict[str, Any]:
        return {"promptFeedback": {"blockReason": "SAFETY"}}

    extractor = GeminiExtractor(model="gemini-flash-lite-latest", api_key="k", post=post)

    with pytest.raises(ValueError, match="SAFETY"):
        extractor.extract_page("# Page", {})


def test_truncation_at_the_token_cap_is_called_out(caplog: pytest.LogCaptureFixture) -> None:
    post = RecordingPost('{"concerts": []}', finish_reason="MAX_TOKENS", candidatesTokenCount=4096)
    extractor = GeminiExtractor(model="gemini-flash-lite-latest", api_key="k", post=post)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        extractor.extract_page("# Page", {})

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("maxOutputTokens" in m and "4096" in m for m in warnings)


def test_a_finished_answer_is_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    post = RecordingPost('{"concerts": []}', finish_reason="STOP")
    extractor = GeminiExtractor(model="gemini-flash-lite-latest", api_key="k", post=post)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        extractor.extract_page("# Page", {})

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_tuning_bounds_the_answer() -> None:
    assert GeminiTuning(max_output_tokens=4096).options() == {"temperature": 0, "maxOutputTokens": 4096}


def test_tuning_omits_unset_bounds() -> None:
    assert GeminiTuning().options() == {"temperature": 0}


def test_a_rate_limit_is_retried_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 used to propagate straight out of _http_post and abort the whole
    extract run; it must instead go through composer_http's retry/backoff, the
    same as every other HTTP source in this codebase."""
    monkeypatch.setattr("composer_http.time.sleep", lambda _seconds: None)
    request = httpx.Request("POST", "https://example.invalid/v1beta/models/x:generateContent")
    body = {"candidates": [{"content": {"parts": [{"text": '{"concerts": []}'}]}, "finishReason": "STOP"}]}
    responses = iter(
        [
            httpx.Response(429, request=request, text="rate limited"),
            httpx.Response(200, request=request, json=body),
        ]
    )
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: next(responses))

    extractor = GeminiExtractor(model="gemini-flash-lite-latest", api_key="k")

    assert extractor.extract_page("# Page", {}) == PageExtraction()


def test_pacing_holds_a_minimum_gap_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extraction is sequential, so without a proactive floor on the gap between
    requests a run fires as fast as the model answers and only reacts to the
    free-tier per-minute quota after a 429 — which a long run can then hit
    repeatedly, since one backoff doesn't stop the next chunk from immediately
    breaching it again."""
    request = httpx.Request("POST", "https://example.invalid/v1beta/models/x:generateContent")
    body = {"candidates": [{"content": {"parts": [{"text": '{"concerts": []}'}]}, "finishReason": "STOP"}]}
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: httpx.Response(200, request=request, json=body)
    )

    clock = [0.0]
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("composer_extract.gemini_pacing.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("composer_extract.gemini_pacing.time.sleep", fake_sleep)

    extractor = GeminiExtractor(model="gemini-flash-lite-latest", api_key="k").with_pacing(4.0)
    extractor.extract_page("# Page", {})
    extractor.extract_page("# Page", {})

    assert sleeps == [4.0]


def test_daily_limit_stops_the_run_once_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The free tier's daily quota doesn't reopen until the next reset, so unlike
    a 429 there's nothing to back off and retry into — the run should stop on its
    own instead of hammering a wall until the retries run out."""
    request = httpx.Request("POST", "https://example.invalid/v1beta/models/x:generateContent")
    body = {"candidates": [{"content": {"parts": [{"text": '{"concerts": []}'}]}, "finishReason": "STOP"}]}
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: httpx.Response(200, request=request, json=body)
    )

    extractor = GeminiExtractor(model="gemini-flash-lite-latest", api_key="k").with_daily_limit(2)

    assert extractor.extract_page("# Page 1", {}) == PageExtraction()
    assert extractor.extract_page("# Page 2", {}) == PageExtraction()
    with pytest.raises(RuntimeError, match="daily request limit"):
        extractor.extract_page("# Page 3", {})


def test_request_options_reflect_the_configured_token_cap() -> None:
    extractor = GeminiExtractor(
        model="gemini-flash-lite-latest",
        api_key="k",
        tuning=GeminiTuning(max_output_tokens=2048),
        post=RecordingPost("{}"),
    )

    assert extractor.request_options() == {"temperature": 0, "maxOutputTokens": 2048}
