"""Polite HTTP plumbing shared by every network-facing package.

Two things belong here because both the per-source adapters
(:mod:`composer_scrapers`) and the generic crawler (:mod:`composer_crawler`)
need them, and neither package depends on the other:

* the **contact identity** advertised to the sites we fetch — a User-Agent
  naming a reachable human, which is the whole of politeness as far as those
  sites can see;
* **retrying a request**, since every API here rate-limits or flakes eventually.

The crawler only uses the identity half (crawl4ai does its own fetching,
retrying and rate limiting); the adapters use all of it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

import httpx

DEFAULT_RETRIES = 3

#: Default per-request timeout. Sources whose payload is a multi-megabyte dump
#: raise it at the call site rather than everyone paying for the slowest one.
DEFAULT_TIMEOUT_S = 30.0

T = TypeVar("T")

log = logging.getLogger(__name__)


def contact_email() -> str:
    """Contact email advertised in User-Agent headers.

    Polite fetching means the sites we crawl can reach whoever runs the
    scraper, so ``SCRAPER_CONTACT_EMAIL`` must be set — there is no default.
    Read at call time, not import time, so the environment can be set after
    the module is imported.
    """
    from composer_config import settings

    email = settings.scraper_contact_email
    if not email:
        raise RuntimeError(
            "SCRAPER_CONTACT_EMAIL is not set; scrapers and crawlers must advertise a reachable contact email"
        )
    return email


def user_agent() -> str:
    """User-Agent for API and HTML sources, and for the crawler's browser."""
    return f"composer-ingest/0.1 (research; {contact_email()})"


def browser_user_agent() -> str:
    """Browser-style User-Agent for PDF hosts that reject non-browser agents."""
    return f"Mozilla/5.0 (compatible; composer-ingest/0.1; research; {contact_email()})"


def new_client(
    *, timeout: float = DEFAULT_TIMEOUT_S, headers: Mapping[str, str] | None = None
) -> httpx.Client:
    """An httpx client identified by our contact User-Agent.

    *headers* is merged over the User-Agent, so a source that needs its own
    header (``Accept-Language``, say) does not have to rebuild the identity.
    """
    merged = {"User-Agent": user_agent(), **(headers or {})}
    return httpx.Client(headers=merged, timeout=timeout)


def call_with_retries(
    do_request: Callable[[], T],
    *,
    label: str,
    retries: int = DEFAULT_RETRIES,
    retry_on: tuple[type[Exception], ...] = (httpx.HTTPError,),
) -> T:
    """Run ``do_request`` with exponential backoff (2**attempt seconds).

    When a retryable exception is an :class:`httpx.HTTPStatusError` carrying a
    numeric ``Retry-After`` header (rate limiters answer 429 + Retry-After),
    the wait becomes ``max(backoff, Retry-After)``.
    """
    for attempt in range(1, retries + 1):
        try:
            return do_request()
        except retry_on as exc:
            if attempt == retries:
                raise
            wait = 2**attempt
            if isinstance(exc, httpx.HTTPStatusError):
                retry_after = exc.response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = max(wait, int(retry_after))
            log.warning("%s failed (%s), retrying in %ds", label, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def get_text(client: httpx.Client, url: str, *, label: str, retries: int = DEFAULT_RETRIES) -> str:
    """GET *url* as text, retrying transport errors and error statuses."""

    def do() -> str:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text

    return call_with_retries(do, label=label, retries=retries)


def get_json(client: httpx.Client, url: str, *, label: str, retries: int = DEFAULT_RETRIES) -> dict[str, Any]:
    """GET *url* as a JSON object, retrying.

    ``ValueError`` is retryable here where :func:`get_text` leaves it alone: a
    body truncated in flight still arrives as a 200 and only fails at the JSON
    decode, so the decode failure is the transport error surfacing late.
    """

    def do() -> dict[str, Any]:
        resp = client.get(url)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    return call_with_retries(do, label=label, retries=retries, retry_on=(httpx.HTTPError, ValueError))
