"""HTTP access to classical-music-online.net.

A two-level crawl: 26 alphabet index pages discover the composers, and each
composer's own page is fetched for its works. The site serves cp1251 and
declares it in the ``Content-Type`` header, so httpx decodes ``resp.text``
correctly — do not decode the bytes by hand.

The index pages are enumerators and stay live; composer pages are mirrored.
"""

from __future__ import annotations

import logging
import string
from collections.abc import Iterator

from composer_http import SourceSession

from .composers import IndexEntry, iter_index_entries

BASE_URL = "https://classical-music-online.net"
REQUEST_DELAY_S = 0.5
RETRIES = 3

log = logging.getLogger(__name__)


def fetch_index(session: SourceSession, letter: str) -> str:
    """Fetch one letter of the composer index."""
    return session.get_text(f"{BASE_URL}/en/composers/{letter}", label=f"index {letter}", retries=RETRIES)


def iter_composers(session: SourceSession, max_pages: int | None = None) -> Iterator[tuple[IndexEntry, str]]:
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
    for letter in string.ascii_uppercase:
        page = fetch_index(session, letter)
        entries = iter_index_entries(page, BASE_URL, letter)
        log.info("classicalmusiconline index %s: %d composers", letter, len(entries))
        for entry in entries:
            if entry.external_id in seen:
                continue
            seen.add(entry.external_id)
            detail = session.try_get_text(
                entry.url, label=f"composer {entry.external_id}", mirror=True, retries=RETRIES
            )
            if detail is None:
                continue
            yield entry, detail
            count += 1
            if max_pages is not None and count >= max_pages:
                return
