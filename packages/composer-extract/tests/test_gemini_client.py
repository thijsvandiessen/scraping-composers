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


def test_request_options_reflect_the_configured_token_cap() -> None:
    extractor = GeminiExtractor(
        model="gemini-flash-lite-latest",
        api_key="k",
        tuning=GeminiTuning(max_output_tokens=2048),
        post=RecordingPost("{}"),
    )

    assert extractor.request_options() == {"temperature": 0, "maxOutputTokens": 2048}
