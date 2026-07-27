"""Ollama-backed extractor: one chat call per markdown chunk, structured output.

The model is asked to return JSON matching :class:`PageExtraction`'s schema
(Ollama's ``format=`` structured output), which is then validated back into the
typed model. The ``chat`` callable is injectable so tests can stand in for a
running model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import ollama
from composer_config import settings
from pydantic import BaseModel

from .prompt import RECORDING_SYSTEM_PROMPT, SYSTEM_PROMPT, build_user_prompt
from .schema import PageExtraction, PageRecordingExtraction

log = logging.getLogger(__name__)

ChatFn = Callable[..., Any]

_M = TypeVar("_M", bound=BaseModel)

#: ``done_reason`` when the model was cut off at ``num_predict`` rather than
#: finishing its answer. Truncated JSON can only fail validation, so it is worth
#: naming the cause here instead of leaving the generic "unusable output" warning
#: that :mod:`.resilience` emits downstream as the only clue.
_TRUNCATED = "length"


def _field(response: Any, name: str) -> Any:
    """One field of an ollama response, which is an object or a mapping."""
    value = getattr(response, name, None)
    if value is None and isinstance(response, dict):
        value = response.get(name)
    return value


def _response_content(response: Any) -> str:
    """The assistant text from an ollama ChatResponse (object or mapping)."""
    message = _field(response, "message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not content:
        raise ValueError("ollama response had no message content")
    return content


def _log_response(model: str, response: Any, chars: int, seconds: float) -> None:
    """Report what the model did with one chunk: how long, how many tokens, and
    whether it actually finished."""
    done_reason = _field(response, "done_reason")
    log.debug(
        "extract: %s answered in %.1fs (%d chars, prompt_eval=%s, eval=%s, done_reason=%s)",
        model,
        seconds,
        chars,
        _field(response, "prompt_eval_count"),
        _field(response, "eval_count"),
        done_reason,
    )
    if done_reason == _TRUNCATED:
        log.warning(
            "extract: %s hit the num_predict cap after %s token(s); the answer is truncated "
            "and cannot validate",
            model,
            _field(response, "eval_count"),
        )


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
        resolved = model or settings.ollama_model
        log.info(
            "extract: using ollama model %s at %s (num_ctx=%s, num_predict=%s, timeout=%.0fs)",
            resolved,
            settings.ollama_base_url,
            settings.ollama_num_ctx,
            settings.ollama_num_predict,
            settings.ollama_timeout_s,
        )
        return cls(
            model=resolved,
            host=settings.ollama_base_url,
            tuning=OllamaTuning(num_ctx=settings.ollama_num_ctx, num_predict=settings.ollama_num_predict),
            timeout_s=settings.ollama_timeout_s,
            chat=chat,
        )

    def _extract(self, markdown: str, metadata: dict[str, str], system_prompt: str, schema: type[_M]) -> _M:
        log.debug(
            "extract: asking %s for %s from %d chars of markdown (%d metadata key(s))",
            self._model,
            schema.__name__,
            len(markdown),
            len(metadata),
        )
        started = time.monotonic()
        response = self._chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_user_prompt(markdown, metadata)},
            ],
            format=schema.model_json_schema(),
            options=self._tuning.options(),
        )
        content = _response_content(response)
        _log_response(self._model, response, len(content), time.monotonic() - started)
        return schema.model_validate_json(content)

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        return self._extract(markdown, metadata, SYSTEM_PROMPT, PageExtraction)

    def extract_recording_page(self, markdown: str, metadata: dict[str, str]) -> PageRecordingExtraction:
        return self._extract(markdown, metadata, RECORDING_SYSTEM_PROMPT, PageRecordingExtraction)
