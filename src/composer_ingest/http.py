"""Shared HTTP access with retries — the one place the retry/backoff/Retry-After
loop lives, so sources don't each re-implement it.

Wrap an ``httpx.Client`` in :class:`Http` and call ``get_json`` / ``get_text``
/ ``post_json`` / ``request_text``. Each call retries on transport errors,
non-2xx responses and malformed bodies with exponential backoff (2, 4, 8, ...
seconds); when ``honor_retry_after`` is set, a ``Retry-After`` header overrides
the backoff. POSTs are used by callers (e.g. SPARQL) whose responses must not
hit an edge cache.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_RETRIES = 3


class Http:
    """A thin retrying wrapper around one ``httpx.Client``."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        retries: int = DEFAULT_RETRIES,
        honor_retry_after: bool = False,
    ) -> None:
        self.client = client
        self.retries = retries
        self.honor_retry_after = honor_retry_after

    def get_json(self, url: str, *, desc: str | None = None, **kwargs: Any) -> Any:
        return self._request("GET", url, lambda r: r.json(), desc or f"GET {url}", **kwargs)

    def get_text(self, url: str, *, desc: str | None = None, **kwargs: Any) -> str:
        return self._request("GET", url, lambda r: r.text, desc or f"GET {url}", **kwargs)

    def post_json(
        self,
        url: str,
        *,
        extract: Callable[[Any], Any] | None = None,
        desc: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """POST and parse JSON. ``extract`` runs inside the retried block so a
        body that is missing expected keys (e.g. a truncated SPARQL response) is
        retried rather than raising straight through."""

        def parse(resp: httpx.Response) -> Any:
            data = resp.json()
            return extract(data) if extract is not None else data

        return self._request("POST", url, parse, desc or f"POST {url}", **kwargs)

    def request_text(self, method: str, url: str, *, desc: str | None = None, **kwargs: Any) -> str:
        return self._request(method, url, lambda r: r.text, desc or f"{method} {url}", **kwargs)

    def _request(
        self,
        method: str,
        url: str,
        parse: Callable[[httpx.Response], T],
        desc: str,
        **kwargs: Any,
    ) -> T:
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.client.request(method, url, **kwargs)
                resp.raise_for_status()
                return parse(resp)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                if attempt == self.retries:
                    raise
                wait = 2**attempt
                if self.honor_retry_after and isinstance(exc, httpx.HTTPStatusError):
                    retry_after = exc.response.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        wait = max(wait, int(retry_after))
                log.warning("%s failed (%s), retrying in %ds", desc, exc, wait)
                time.sleep(wait)
        raise AssertionError("unreachable")
