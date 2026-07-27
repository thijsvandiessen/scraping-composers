"""The bounds handed to Ollama, and the strictness of the parse that follows."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from composer_config import settings
from composer_extract import OllamaExtractor, OllamaTuning
from composer_extract.schema import PageExtraction

_LOGGER = "composer_extract.client"


class RecordingChat:
    """Stands in for ``ollama.Client.chat``, returning *content* verbatim.

    *metrics* are the bookkeeping fields a real ChatResponse carries alongside the
    message (``done_reason``, token counts).
    """

    def __init__(self, content: str, **metrics: Any) -> None:
        self._content = content
        self._metrics = metrics
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return {"message": {"content": self._content}, **self._metrics}


def test_tuning_bounds_both_the_prompt_and_the_answer() -> None:
    options = OllamaTuning(num_ctx=16384, num_predict=4096).options()

    assert options == {"temperature": 0, "num_ctx": 16384, "num_predict": 4096}


def test_tuning_omits_unset_bounds() -> None:
    assert OllamaTuning().options() == {"temperature": 0}


def test_from_settings_passes_the_configured_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ollama_num_ctx", 8192)
    monkeypatch.setattr(settings, "ollama_num_predict", 2048)
    chat = RecordingChat('{"concerts": []}')

    OllamaExtractor.from_settings(chat=chat).extract_page("# Page", {})

    assert chat.kwargs["options"] == {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}


def test_response_is_validated_not_salvaged() -> None:
    """A truncated answer is a failure, never a partial result."""
    truncated = '{"concerts": [{"date": "2024-05-01", "soloists": [{"name": "X", "discipline": "Piano'
    extractor = OllamaExtractor(model="qwen2.5", chat=RecordingChat(truncated))

    with pytest.raises(ValueError):
        extractor.extract_page("# Page", {})


def test_empty_response_is_a_failure() -> None:
    extractor = OllamaExtractor(model="qwen2.5", chat=RecordingChat(""))

    with pytest.raises(ValueError, match="no message content"):
        extractor.extract_page("# Page", {})


def test_a_valid_response_parses() -> None:
    extractor = OllamaExtractor(model="qwen2.5", chat=RecordingChat('{"concerts": []}'))

    assert extractor.extract_page("# Page", {}) == PageExtraction()


def test_truncation_at_the_token_cap_is_called_out(caplog: pytest.LogCaptureFixture) -> None:
    """``done_reason: length`` means the answer was cut off at ``num_predict``, which
    is the usual reason the JSON below cannot validate. Downstream all you see is a
    generic "unusable output" warning, so the cause is named here."""
    chat = RecordingChat('{"concerts": []}', done_reason="length", eval_count=4096)
    extractor = OllamaExtractor(model="qwen2.5", chat=chat)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        extractor.extract_page("# Page", {})

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("num_predict" in m and "4096" in m for m in warnings)


def test_a_finished_answer_is_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    chat = RecordingChat('{"concerts": []}', done_reason="stop", eval_count=120)
    extractor = OllamaExtractor(model="qwen2.5", chat=chat)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        extractor.extract_page("# Page", {})

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_each_call_reports_its_cost_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Latency and token counts per chunk are what tell you whether a multi-hour
    extract is slow because of the model or because of the pages."""
    chat = RecordingChat('{"concerts": []}', done_reason="stop", prompt_eval_count=900, eval_count=120)
    extractor = OllamaExtractor(model="qwen2.5", chat=chat)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        extractor.extract_page("# Page", {})

    messages = [r.getMessage() for r in caplog.records]
    assert any("prompt_eval=900" in m and "eval=120" in m for m in messages)
