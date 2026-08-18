"""Picks the extraction backend a run actually uses.

``OllamaExtractor`` and ``GeminiExtractor`` share the same shape by convention
(``model``, ``request_options``, ``with_cache``, ``extract_page``/
``extract_recording_page``/``extract_claim_page``) rather than a common base
class — the same duck-typed :class:`~typing.Protocol` pattern
:mod:`.extract` and :mod:`.claims` already use for "anything that can extract
a page". This module is the one place that turns ``settings.llm_provider``
into a concrete instance, so a third backend is one branch here rather than
a ternary at every call site.
"""

from __future__ import annotations

from composer_config import settings

from .cache import ExtractCache
from .client import OllamaExtractor
from .gemini_client import GeminiExtractor

Extractor = OllamaExtractor | GeminiExtractor

_PROVIDERS = ("ollama", "gemini")


def create_extractor(
    *, provider: str | None = None, model: str | None = None, cache: ExtractCache | None = None
) -> Extractor:
    """The extractor for *provider* (default ``settings.llm_provider``), ready to use.

    *model* overrides that provider's configured model name (as the CLI's
    ``--model`` flag does); *cache* is wired in the same way for either backend.
    """
    provider = provider or settings.llm_provider
    if provider == "ollama":
        return OllamaExtractor.from_settings(model=model, cache=cache)
    if provider == "gemini":
        return GeminiExtractor.from_settings(model=model, cache=cache)
    raise ValueError(f"unknown llm_provider {provider!r}; expected one of {_PROVIDERS}")
