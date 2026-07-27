"""The bounds handed to Ollama, and the strictness of the parse that follows."""

from __future__ import annotations

from typing import Any

import pytest
from composer_config import settings
from composer_extract import OllamaExtractor, OllamaTuning
from composer_extract.schema import PageExtraction


class RecordingChat:
    """Stands in for ``ollama.Client.chat``, returning *content* verbatim."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return {"message": {"content": self._content}}


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
