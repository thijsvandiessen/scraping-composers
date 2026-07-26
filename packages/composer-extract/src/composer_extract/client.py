"""Ollama-backed extractor: one chat call per markdown chunk, structured output.

The model is asked to return JSON matching :class:`PageExtraction`'s schema
(Ollama's ``format=`` structured output), which is then validated back into the
typed model. The ``chat`` callable is injectable so tests can stand in for a
running model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import ollama
from composer_config import settings

from .prompt import SYSTEM_PROMPT, build_user_prompt
from .schema import PageExtraction

ChatFn = Callable[..., Any]


def _response_content(response: Any) -> str:
    """The assistant text from an ollama ChatResponse (object or mapping)."""
    message = getattr(response, "message", None)
    if message is None and isinstance(response, dict):
        message = response.get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not content:
        raise ValueError("ollama response had no message content")
    return content


class OllamaExtractor:
    """Extract a page's concerts with a local Ollama model."""

    def __init__(
        self,
        *,
        model: str,
        host: str | None = None,
        num_ctx: int | None = None,
        chat: ChatFn | None = None,
    ) -> None:
        self._model = model
        self._num_ctx = num_ctx
        self._chat: ChatFn = chat if chat is not None else ollama.Client(host=host).chat

    @classmethod
    def from_settings(cls, *, model: str | None = None, chat: ChatFn | None = None) -> OllamaExtractor:
        return cls(
            model=model or settings.ollama_model,
            host=settings.ollama_base_url,
            num_ctx=settings.ollama_num_ctx,
            chat=chat,
        )

    def _options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": 0}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        return options

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        response = self._chat(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(markdown, metadata)},
            ],
            format=PageExtraction.model_json_schema(),
            options=self._options(),
        )
        return PageExtraction.model_validate_json(_response_content(response))
