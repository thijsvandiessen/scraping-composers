"""One source's conversation with the network: the shape every adapter fetches through.

Every per-source adapter used to assemble the same four things by hand — a
client, a pause between requests, retries, and (for the sources that spend one
request per record) a lookup in the page mirror before each fetch and a write
after it. :class:`SourceSession` is those four things once, so a source's
``fetch`` module only says *what* to ask for:

* **Politeness.** Consecutive network requests (retries included) are at least
  ``delay_s`` apart. A page served from the mirror costs no request, so it costs
  no pause either.
* **Retries.** Every request goes through :func:`~composer_http.call_with_retries`.
* **The mirror** (:class:`~composer_http.PageCache`), opted into per request with
  ``mirror=True``. What is mirrored is the source's detail pages; what is *not*
  is anything that enumerates the source (an index, a listing, a sitemap), since
  that is how a re-run learns the source has grown — served from a mirror it
  would pin every later run to the records that existed the first time.
  Mirrored pages expire after ``max_age`` (the adapter's cadence interval), and a
  mirrored POST is fingerprinted by its query, so a changed query is a miss.
* **Tolerance**, opted into with the ``try_`` methods: a long sweep must not be
  lost to one 404, so those log the failure and return ``None`` — and a failure
  is never mirrored, so the next run retries it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from types import TracebackType
from typing import Any, Self

import httpx

from .pages import PageCache

__all__ = ["SourceSession", "Validator"]

log = logging.getLogger(__name__)

DEFAULT_RETRIES = 3

#: Decides whether a fetched body is worth mirroring. A body it rejects is still
#: returned to the caller — it is only kept out of the mirror, so it is refetched.
Validator = Callable[[str], bool]


class SourceSession:
    """A polite, retrying, mirrored HTTP session for one source.

    Use as a context manager; leaving it closes the client.
    """

    def __init__(
        self,
        source: str,
        client: httpx.Client,
        *,
        delay_s: float = 0.5,
        max_age: timedelta | None = None,
        cache: PageCache | None = None,
    ) -> None:
        self.source = source
        self.client = client
        self.delay_s = delay_s
        self.max_age = max_age
        self.cache = cache
        self.requests = 0
        self._last_request: float | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.client.close()

    # -- the network ------------------------------------------------------

    def _pause(self) -> None:
        """Sleep until ``delay_s`` has passed since the previous network request."""
        if self._last_request is not None and self.delay_s > 0:
            remaining = self.delay_s - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)

    def _send(self, method: str, url: str, *, label: str, retries: int, **request: Any) -> httpx.Response:
        """One request, retried on transport errors and error statuses."""
        from . import call_with_retries

        def do() -> httpx.Response:
            self._pause()
            try:
                response = self.client.request(method, url, **request)
            finally:
                self.requests += 1
                self._last_request = time.monotonic()
            response.raise_for_status()
            return response

        return call_with_retries(do, label=label, retries=retries)

    # -- the mirror ---------------------------------------------------------

    def lookup(self, key: str, *, fingerprint: str | None = None) -> str | None:
        """The fresh body mirrored under *key*, if any (see :meth:`mirrored`)."""
        if self.cache is None:
            return None
        return self.cache.get(key, fingerprint=fingerprint, max_age=self.max_age)

    def store(self, key: str, body: str, *, fingerprint: str | None = None) -> None:
        """Mirror *body* under *key* (see :meth:`mirrored`)."""
        if self.cache is not None:
            self.cache.put(key, body, fingerprint=fingerprint)

    def mirrored(
        self,
        key: str,
        produce: Callable[[], str],
        *,
        fingerprint: str | None = None,
        valid: Validator | None = None,
    ) -> str:
        """The body mirrored under *key*, or *produce*'s answer, mirrored if *valid*.

        The general form behind ``mirror=True``, for requests that are not one
        GET per record — a POSTed query, whose *fingerprint* should then be
        :func:`~composer_http.request_fingerprint` of it, so that changing the
        query invalidates the answer. A request that answers for many records
        at once (a GraphQL batch, say) uses :meth:`lookup` and :meth:`store` per
        record instead, fingerprinting each with the part of the query that
        produced it.
        """
        stored = self.lookup(key, fingerprint=fingerprint)
        if stored is not None:
            return stored
        body = produce()
        if valid is None or valid(body):
            self.store(key, body, fingerprint=fingerprint)
        return body

    # -- requests -----------------------------------------------------------

    def get_text(
        self,
        url: str,
        *,
        label: str,
        mirror: bool = False,
        valid: Validator | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> str:
        """GET *url* as text; raises once retries are spent.

        With ``mirror``, served from the page mirror when it holds a fresh copy,
        and stored there when fetched (and *valid* accepts it). A GET is
        identified by its URL alone, so it carries no fingerprint — which also
        keeps mirrors written before fingerprints existed valid.
        """

        def produce() -> str:
            return self._send("GET", url, label=label, retries=retries).text

        return self.mirrored(url, produce, valid=valid) if mirror else produce()

    def try_get_text(
        self,
        url: str,
        *,
        label: str,
        mirror: bool = False,
        valid: Validator | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> str | None:
        """:meth:`get_text`, but a failure is logged and answered with ``None``."""
        try:
            return self.get_text(url, label=label, mirror=mirror, valid=valid, retries=retries)
        except httpx.HTTPError as exc:
            log.warning("%s: skipping %s: %s", self.source, label, exc)
            return None

    def get_bytes(self, url: str, *, label: str, retries: int = DEFAULT_RETRIES) -> bytes:
        """GET *url* as raw bytes (a gzipped sitemap, say). Never mirrored."""
        return self._send("GET", url, label=label, retries=retries).content

    def get_json(
        self, url: str, *, label: str, mirror: bool = False, retries: int = DEFAULT_RETRIES
    ) -> dict[str, Any]:
        """GET *url* as a JSON object, retrying a body that does not decode.

        As in :func:`composer_http.get_json`: a body truncated in flight still
        arrives as a 200, so the decode failure is the transport error surfacing
        late. Only a body that decodes to an object reaches the mirror.
        """
        from . import call_with_retries

        def produce() -> str:
            def do() -> str:
                text = self._send("GET", url, label=label, retries=1).text
                _json_object(text, label)
                return text

            return call_with_retries(do, label=label, retries=retries, retry_on=(httpx.HTTPError, ValueError))

        if not mirror:
            return _json_object(produce(), label)
        text = self.mirrored(url, produce)
        try:
            return _json_object(text, label)
        except ValueError:
            # an unreadable mirror entry is a miss, not a crash
            text = produce()
            self.store(url, text)
            return _json_object(text, label)

    def post_text(self, url: str, *, label: str, retries: int = DEFAULT_RETRIES, **request: Any) -> str:
        """POST to *url* and return the body as text.

        *request* is passed to :meth:`httpx.Client.request` (``json=``,
        ``data=``, ``files=``). Never mirrored by itself: wrap it in
        :meth:`mirrored`, fingerprinted by the query, to mirror a POST.
        """
        return self._send("POST", url, label=label, retries=retries, **request).text

    def summary(self) -> str:
        """Requests made and what the mirror saved, for the end-of-run log line."""
        mirror = self.cache.summary() if self.cache is not None else "off"
        return f"{self.requests} requests; page mirror: {mirror}"


def _json_object(text: str, label: str) -> dict[str, Any]:
    """*text* decoded as a JSON object; ``ValueError`` when it is anything else."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{label}: expected a JSON object, got {type(data).__name__}")
    return data  # pyright: ignore[reportUnknownVariableType]
