"""HTTP access to classical-music-online.net.

A two-level crawl: 26 alphabet index pages discover the composers, and each
composer's own page is fetched for its works. The site serves cp1251 and
declares it in the ``Content-Type`` header, so httpx decodes ``resp.text``
correctly — do not decode the bytes by hand.
"""

from __future__ import annotations

import logging
import string
import time
from collections.abc import Iterator

import httpx

from .._http import call_with_retries, user_agent
from .composers import IndexEntry, iter_index_entries

BASE_URL = "https://classical-music-online.net"
REQUEST_DELAY_S = 0.5
RETRIES = 3

log = logging.getLogger(__name__)


def _make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent()}, timeout=30)


def _get_text(client: httpx.Client, url: str, label: str) -> str:
    def do() -> str:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text

    return call_with_retries(do, label=label, retries=RETRIES)


def fetch_index(client: httpx.Client, letter: str) -> str:
    """Fetch one letter of the composer index."""
    return _get_text(client, f"{BASE_URL}/en/composers/{letter}", f"index {letter}")


def iter_composers(max_pages: int | None = None) -> Iterator[tuple[IndexEntry, str]]:
    """Yield (index entry, composer page HTML) for every listed composer.

    Walks the index A-Z, fetching each letter only when it is reached, and
    fetches every composer's page for its works. ``max_pages`` caps the number
    of composer pages fetched (not index pages), for test runs; the full crawl
    is ~11.6k composers and takes a couple of hours at the request delay.

    A composer page that fails after its retries is logged and skipped rather
    than abandoning the run.
    """
    seen: set[str] = set()
    count = 0
    with _make_client() as client:
        for letter in string.ascii_uppercase:
            page = fetch_index(client, letter)
            entries = iter_index_entries(page, BASE_URL, letter)
            log.info("classicalmusiconline index %s: %d composers", letter, len(entries))
            time.sleep(REQUEST_DELAY_S)
            for entry in entries:
                if entry.external_id in seen:
                    continue
                seen.add(entry.external_id)
                try:
                    detail = _get_text(client, entry.url, f"composer {entry.external_id}")
                except httpx.HTTPError as exc:
                    log.warning("skipping composer %s: %s", entry.url, exc)
                    continue
                yield entry, detail
                count += 1
                if max_pages is not None and count >= max_pages:
                    return
                time.sleep(REQUEST_DELAY_S)
