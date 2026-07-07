"""HTTP access to the Concertgebouworkest archive (both views are one request).

The search page is a plain GET; the List view is a multipart POST of the
search form's "List" button with no filters.
"""

from __future__ import annotations

import logging
import time

import httpx

BASE_URL = "https://archief.concertgebouworkest.nl"
SEARCH_URL = BASE_URL + "/en/archive/search/"
RETRIES = 3

log = logging.getLogger(__name__)


def _fetch(label: str, **request: object) -> str:
    with httpx.Client(
        headers={"User-Agent": "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"},
        timeout=30,
    ) as client:
        for attempt in range(1, RETRIES + 1):
            try:
                resp = client.request(**request)  # pyright: ignore[reportArgumentType]
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPError as exc:
                if attempt == RETRIES:
                    raise
                wait = 2**attempt
                log.warning("%s fetch failed (%s), retrying in %ds", label, exc, wait)
                time.sleep(wait)
    raise AssertionError("unreachable")


def _fetch_search_page() -> str:
    return _fetch("search page", method="GET", url=SEARCH_URL)


def _fetch_list_page() -> str:
    # submitting the form's "List" button with no filters returns every concert
    # as multipart/form-data; a plain GET of the list tab does not work
    return _fetch("list view", method="POST", url=SEARCH_URL, files={"list": (None, "List")})
