"""HTTP access to the Vienna Philharmonic concert archive.

The archive page ships an empty ``<div id="content">``; its own ``archive.js``
reads the item count out of ``#totalItemCount``, divides it by a page size of
1000 and requests ``/en/konzert-archiv/<n>`` for each resulting page, appending
the fragments into that div. The "Show more" button only reveals what is already
on its way, so fetching those fragments directly *is* the whole result listing:
one landing page (for the filter vocabularies and the item count) plus eleven
result fragments at the time of writing.

The concert *detail* pages are the expensive half: one request per concert, so
10,749 of them. They are fetched at a slower rate than the fragments, and
through the session's page mirror — the archive is a historical record (the
source's cadence is ``STATIC``), so a page fetched once never needs fetching
again, and a sweep interrupted after two hours resumes where it stopped rather
than starting over. The landing page and the fragments are the enumerators and
always run live, so a re-run picks up the concerts given since.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterator

from composer_http import SourceSession

log = logging.getLogger(__name__)

BASE_URL = "https://www.wienerphilharmoniker.at"
ARCHIVE_URL = BASE_URL + "/en/konzert-archiv"
#: Between requests. There are four figures' worth of detail pages, so the
#: sweep is hours long either way and there is no reason to hurry it; the dozen
#: fragments pay the same rate.
REQUEST_DELAY_S = 1.0

#: Concerts per result fragment. Hard-coded in the site's archive.js, and the
#: divisor it uses to decide how many fragments exist.
PAGE_SIZE = 1000

# <div id="totalItemCount" data-count=10749></div> — the count is served
# unquoted, so both quoted and bare forms are accepted.
_TOTAL_COUNT = re.compile(r'id="totalItemCount"[^>]*\bdata-count=["\']?(\d+)')


def total_item_count(landing: str) -> int:
    """The number of concerts the archive says it holds."""
    match = _TOTAL_COUNT.search(landing)
    if match is None:
        raise ValueError("#totalItemCount not found on the archive page; did the site change?")
    return int(match.group(1))


def page_count(total: int) -> int:
    """How many result fragments hold *total* concerts, as archive.js computes it."""
    return math.ceil(total / PAGE_SIZE)


def fetch_landing(session: SourceSession) -> str:
    """The archive page itself: filter vocabularies and the total item count."""
    return session.get_text(ARCHIVE_URL, label="archive page")


def fetch_fragments(session: SourceSession, landing: str, max_pages: int | None = None) -> Iterator[str]:
    """Yield each result fragment, one request per 1000 concerts.

    ``max_pages`` caps the number of fragments fetched, for test runs.
    """
    pages = page_count(total_item_count(landing))
    if max_pages is not None:
        pages = min(pages, max_pages)
    for number in range(1, pages + 1):
        yield session.get_text(f"{ARCHIVE_URL}/{number}", label=f"archive fragment {number}")


def fetch_detail(session: SourceSession, url: str) -> str | None:
    """One concert's detail page, from the mirror when it holds it.

    Returns ``None`` when the page cannot be fetched, rather than raising: a
    sweep of ten thousand concerts must not be lost to one of them 404ing, and
    the concert still has everything the result listing said about it.
    """
    return session.try_get_text(url, label=f"concert {url}", mirror=True)
