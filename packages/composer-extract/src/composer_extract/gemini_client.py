"""Gemini-backed extractor: one generateContent call per markdown chunk, structured JSON output.

Mirrors :class:`~.client.OllamaExtractor`'s public surface (``model``,
``request_options``, ``with_cache``, ``extract_page``/``extract_recording_page``/
``extract_claim_page``) so :mod:`.provider` can hand either one to the same
extraction pipeline. The ``post`` callable is injectable so tests can stand in
for a live API, the same seam ``OllamaExtractor`` uses for ``chat``. The
Pydantic-to-``responseSchema`` translation lives in :mod:`.gemini_schema`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from composer_config import settings
from composer_http import call_with_retries
from pydantic import BaseModel

from .cache import ExtractCache, request_key
from .gemini_schema import to_gemini_schema
from .prompt import CLAIMS_SYSTEM_PROMPT, RECORDING_SYSTEM_PROMPT, SYSTEM_PROMPT, build_user_prompt
from .schema import PageClaimExtraction, PageExtraction, PageRecordingExtraction

log = logging.getLogger(__name__)

PostFn = Callable[..., dict[str, Any]]

_M = TypeVar("_M", bound=BaseModel)

#: finishReason when the model was cut off at maxOutputTokens rather than
#: finishing its answer; mirrors client.py's _TRUNCATED for the same reason.
_TRUNCATED = "MAX_TOKENS"


def _response_text(response: dict[str, Any]) -> str:
    """The model's answer text from a generateContent response body."""
    candidates = response.get("candidates") or []
    if not candidates:
        block_reason = (response.get("promptFeedback") or {}).get("blockReason")
        raise ValueError(f"gemini response had no candidates (blockReason={block_reason})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts)
    if not text:
        raise ValueError("gemini response had no text content")
    return text


def _log_response(model: str, response: dict[str, Any], chars: int, seconds: float) -> None:
    """Report what the model did with one chunk: how long, how many tokens, and
    whether it actually finished. Mirrors client.py's _log_response."""
    candidate = (response.get("candidates") or [{}])[0]
    finish_reason = candidate.get("finishReason")
    usage = response.get("usageMetadata") or {}
    log.debug(
        "extract: %s answered in %.1fs (%d chars, prompt_tokens=%s, output_tokens=%s, finish_reason=%s)",
        model,
        seconds,
        chars,
        usage.get("promptTokenCount"),
        usage.get("candidatesTokenCount"),
        finish_reason,
    )
    if finish_reason == _TRUNCATED:
        log.warning(
            "extract: %s hit the maxOutputTokens cap after %s token(s); the answer is truncated "
            "and cannot validate",
            model,
            usage.get("candidatesTokenCount"),
        )


@dataclass(frozen=True)
class GeminiTuning:
    """Bounds on a single model call — the Gemini analogue of :class:`~.client.OllamaTuning`."""

    max_output_tokens: int | None = None

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": 0}
        if self.max_output_tokens is not None:
            options["maxOutputTokens"] = self.max_output_tokens
        return options


class GeminiExtractor:
    """Extract a page's concerts or recordings with Google's hosted Gemini API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        tuning: GeminiTuning | None = None,
        timeout_s: float | None = None,
        post: PostFn | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._tuning = tuning if tuning is not None else GeminiTuning()
        self._timeout_s = timeout_s
        self._post: PostFn = post if post is not None else self._http_post
        # Attached after construction rather than a seventh __init__ argument:
        # ruff's max-args is 5 and the signature above is already at the limit.
        self._cache: ExtractCache | None = None

    def with_cache(self, cache: ExtractCache | None) -> GeminiExtractor:
        """Consult *cache* before asking the model. Mutates, and returns ``self``
        so it can be chained straight onto a constructor call."""
        self._cache = cache
        return self

    @property
    def model(self) -> str:
        return self._model

    def request_options(self) -> dict[str, Any]:
        """The generation options every call uses — part of :func:`.ledger.request_fingerprint`,
        since a changed option changes the answer as much as a changed prompt does."""
        return self._tuning.options()

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, post: PostFn | None = None, cache: ExtractCache | None = None
    ) -> GeminiExtractor:
        if not settings.google_ai_api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY is not set; required to use the gemini extraction provider")
        resolved = model or settings.google_ai_model
        log.info(
            "extract: using gemini model %s (max_output_tokens=%s, timeout=%.0fs)",
            resolved,
            settings.google_ai_max_output_tokens,
            settings.google_ai_timeout_s,
        )
        return cls(
            model=resolved,
            api_key=settings.google_ai_api_key,
            tuning=GeminiTuning(max_output_tokens=settings.google_ai_max_output_tokens),
            timeout_s=settings.google_ai_timeout_s,
            post=post,
        ).with_cache(cache)

    def _http_post(
        self, *, model: str, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        def do() -> dict[str, Any]:
            # Read at call time, not construction time, so GOOGLE_AI_BASE_URL can
            # be set after this extractor is built (composer_http.contact_email
            # does the same for the same reason).
            response = httpx.post(
                f"{settings.google_ai_base_url}/models/{model}:generateContent",
                headers={"X-goog-api-key": self._api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "generationConfig": {
                        **self._tuning.options(),
                        "responseMimeType": "application/json",
                        "responseSchema": schema,
                    },
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            return response.json()

        # A free-tier per-minute quota (429) needs longer than composer_http's
        # default 3-attempt backoff (2s+4s) to clear; without this an extract run
        # aborted wholesale on the first rate-limited chunk rather than pausing
        # past it. Gemini does not send a Retry-After header, so this falls back
        # to plain exponential backoff (call_with_retries still honours one if a
        # future response includes it).
        return call_with_retries(do, label=f"gemini {model}", retries=5)

    def _from_cache(self, key: str, schema: type[_M]) -> _M | None:
        """A previously stored answer for *key*, or None on a miss.

        A stored answer that no longer validates (the schema changed shape under
        it, or the row is damaged) is dropped rather than raised, so one bad entry
        costs a single model call instead of failing the page.
        """
        if self._cache is None:
            return None
        payload = self._cache.get(key)
        if payload is None:
            return None
        try:
            return schema.model_validate_json(payload)
        except ValueError as exc:
            log.warning("extract: cached %s no longer validates (%s); re-asking", schema.__name__, exc)
            self._cache.delete(key)
            return None

    def _extract(self, markdown: str, metadata: dict[str, str], system_prompt: str, schema: type[_M]) -> _M:
        user_prompt = build_user_prompt(markdown, metadata)
        pydantic_schema = schema.model_json_schema()
        options = self.request_options()
        key = request_key(
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=pydantic_schema,
            options=options,
        )
        cached = self._from_cache(key, schema)
        if cached is not None:
            log.debug("extract: reusing the cached %s for %d chars", schema.__name__, len(markdown))
            return cached
        log.debug(
            "extract: asking %s for %s from %d chars of markdown (%d metadata key(s))",
            self._model,
            schema.__name__,
            len(markdown),
            len(metadata),
        )
        started = time.monotonic()
        response = self._post(
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=to_gemini_schema(pydantic_schema),
        )
        content = _response_text(response)
        _log_response(self._model, response, len(content), time.monotonic() - started)
        # Validated before it is stored, so unusable output — the truncated JSON
        # .resilience retries on — is never cached.
        extraction = schema.model_validate_json(content)
        if self._cache is not None:
            self._cache.put(key, model=self._model, schema_name=schema.__name__, response=content)
        return extraction

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        return self._extract(markdown, metadata, SYSTEM_PROMPT, PageExtraction)

    def extract_recording_page(self, markdown: str, metadata: dict[str, str]) -> PageRecordingExtraction:
        return self._extract(markdown, metadata, RECORDING_SYSTEM_PROMPT, PageRecordingExtraction)

    def extract_claim_page(self, markdown: str, metadata: dict[str, str]) -> PageClaimExtraction:
        return self._extract(markdown, metadata, CLAIMS_SYSTEM_PROMPT, PageClaimExtraction)
