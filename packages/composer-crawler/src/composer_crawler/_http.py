"""Shared HTTP plumbing for the generic crawler: contact identity and retrying requests.

Mirrors ``composer_scrapers._http``; composer-scrapers is a peer package (and its
import pulls in heavy parsing dependencies), so the small helpers are duplicated
here instead of imported.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

DEFAULT_RETRIES = 3

T = TypeVar("T")

log = logging.getLogger(__name__)


def contact_email() -> str:
    """Contact email advertised in User-Agent headers.

    Polite crawling means the crawled sites can reach whoever runs the
    crawler, so ``SCRAPER_CONTACT_EMAIL`` must be set — there is no default.
    Read at call time, not import time, so the environment can be set after
    the module is imported.
    """
    from composer_config import settings

    email = settings.scraper_contact_email
    if not email:
        raise RuntimeError(
            "SCRAPER_CONTACT_EMAIL is not set; crawlers must advertise a reachable contact email"
        )
    return email


def user_agent() -> str:
    """User-Agent for API and HTML sources."""
    return f"composer-ingest/0.1 (research; {contact_email()})"


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
