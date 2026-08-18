"""create_extractor() picks the backend named by settings.llm_provider (or the
explicit override), and rejects anything else before a run gets further."""

from __future__ import annotations

import pytest
from composer_config import settings
from composer_extract import GeminiExtractor, OllamaExtractor, create_extractor


def test_defaults_to_ollama() -> None:
    assert isinstance(create_extractor(), OllamaExtractor)


def test_settings_select_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "google_ai_api_key", "test-key")

    assert isinstance(create_extractor(), GeminiExtractor)


def test_explicit_provider_overrides_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "google_ai_api_key", "test-key")

    assert isinstance(create_extractor(provider="gemini"), GeminiExtractor)


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown llm_provider"):
        create_extractor(provider="claude")
