"""Ollama-backed extractor: one chat call per markdown chunk, structured output.

The model is asked to return JSON matching :class:`PageExtraction`'s schema
(Ollama's ``format=`` structured output), which is then validated back into the
typed model. The ``chat`` callable is injectable so tests can stand in for a
running model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import ollama
from composer_config import settings
from pydantic import BaseModel

from .prompt import RECORDING_SYSTEM_PROMPT, SYSTEM_PROMPT, build_user_prompt
from .schema import PageExtraction, PageRecordingExtraction

ChatFn = Callable[..., Any]

_M = TypeVar("_M", bound=BaseModel)


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


@dataclass(frozen=True)
class OllamaTuning:
    """Bounds on a single model call.

    ``num_ctx`` has to fit a whole chunk plus its answer or Ollama truncates the
    prompt server-side — a truncated page is what sends a model into the
    repetition loop that produces unparseable JSON. ``num_predict`` then caps how
    far such a loop can get before the call returns.
    """

    num_ctx: int | None = None
    num_predict: int | None = None

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": 0}
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        if self.num_predict is not None:
            options["num_predict"] = self.num_predict
        return options


class OllamaExtractor:
    """Extract a page's concerts or recordings with a local Ollama model."""

    def __init__(
        self,
        *,
        model: str,
        host: str | None = None,
        tuning: OllamaTuning | None = None,
        timeout_s: float | None = None,
        chat: ChatFn | None = None,
    ) -> None:
        self._model = model
        self._tuning = tuning if tuning is not None else OllamaTuning()
        self._chat: ChatFn = chat if chat is not None else ollama.Client(host=host, timeout=timeout_s).chat

    @classmethod
    def from_settings(cls, *, model: str | None = None, chat: ChatFn | None = None) -> OllamaExtractor:
        return cls(
            model=model or settings.ollama_model,
            host=settings.ollama_base_url,
            tuning=OllamaTuning(num_ctx=settings.ollama_num_ctx, num_predict=settings.ollama_num_predict),
            timeout_s=settings.ollama_timeout_s,
            chat=chat,
        )

    def _extract(self, markdown: str, metadata: dict[str, str], system_prompt: str, schema: type[_M]) -> _M:
        response = self._chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_user_prompt(markdown, metadata)},
            ],
            format=schema.model_json_schema(),
            options=self._tuning.options(),
        )
        return schema.model_validate_json(_response_content(response))

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        return self._extract(markdown, metadata, SYSTEM_PROMPT, PageExtraction)

    def extract_recording_page(self, markdown: str, metadata: dict[str, str]) -> PageRecordingExtraction:
        return self._extract(markdown, metadata, RECORDING_SYSTEM_PROMPT, PageRecordingExtraction)
