"""HTTP access to rohcollections.org.uk, mirrored page by page.

**robots.txt, and the delay.** The file names ``Googlebot`` and
``Googlebot-image`` with an empty ``Disallow``, then gives ``User-agent: *`` two
groups: one carrying ``Crawl-delay: 180``, one disallowing
``/search/autocomplete*``. Nothing this source reads is disallowed — the
performance index, and the work, production and performance pages, are all
outside that prefix, and the autocomplete endpoint is not used.

The crawl-delay is another matter. Taken literally it is three minutes between
requests, and this source is ~18,000 pages: three weeks of wall clock for one
sweep. :data:`REQUEST_DELAY_S` is set to five seconds instead — a deliberate,
documented departure from what the file asks, chosen with the repository owner
rather than assumed. What makes it defensible is the mirror below: the sweep is
paid once, re-runs cost almost nothing, and five seconds is slower by an order
of magnitude than the half-second the other HTML sources here use. If the site
ever answers 429 or 503, raise this rather than retrying harder — the archive is
a small heritage catalogue on modest hosting, not a CDN.

**The mirror.** ~18,000 pages at ~30KB each — 995 works, ~1,140 productions and
~15,700 nights, measured over a 300-work sample and agreeing with the totals the
site's own search reports (5,690 opera and 9,757 ballet performances). Every
fetch goes through
:class:`~composer_http.PageCache`, which is what makes a sweep this long
survivable: at five seconds a full run is more than a day, and a run that dies
at hour twenty must not start over. It also means the performance tier can be
re-parsed — cast rows are the least regular markup on the site — without going
back for fifteen thousand pages.

The index pages are deliberately *not* mirrored. They are how a re-run learns
that the database has grown; served from a mirror they would pin every later
sweep to the works that existed the first time.
"""

from __future__ import annotations

import logging
import time

import httpx
from composer_http import PageCache, get_text, new_client

from .urls import BASE_URL, index_url

log = logging.getLogger(__name__)

#: Between uncached requests. See the module docstring: robots.txt asks for 180,
#: which is three weeks for one sweep of this source; this is the agreed
#: departure from it, and is still ten times slower than the other HTML sources.
REQUEST_DELAY_S = 5.0

#: The index and the detail pages are the same size and the same server; nothing
#: here needs the default 30s, but a heritage ASP.NET app under load is slow
#: before it is broken, so the timeout is generous rather than tight.
TIMEOUT_S = 60.0


def make_client() -> httpx.Client:
    """A client that follows redirects.

    The site answers ``http`` and the bare apex with a redirect to
    ``https://www.``, and writes some of its own links with an explicit
    ``:443`` that resolves the same way. None of the URLs this source builds
    need a redirect, so this is a second line of defence — but a silent one is
    worth having, because :func:`composer_http.get_text` raises only on 4xx and
    5xx and an unfollowed 301 would return an empty body that parses as a page
    with no records rather than as an error.
    """
    client = new_client(timeout=TIMEOUT_S)
    client.follow_redirects = True
    return client


def fetch_index(client: httpx.Client, letter: str) -> str:
    """One letter of the browse-by-title index.

    Not mirrored, and not tolerant of failure: the index is the only enumerator
    this source has, so a letter that cannot be read is a hole in the sweep that
    would otherwise pass unnoticed as a short run.
    """
    return get_text(client, index_url(letter), label=f"index {letter}")


def fetch_page(client: httpx.Client, url: str, cache: PageCache | None = None) -> str | None:
    """One work, production or performance page, from the mirror when it holds it.

    Returns None rather than raising when the page cannot be fetched. A sweep
    of this length must not be lost to a single 404 or a dropped connection
    eighteen thousand pages in.

    A page that could not be fetched is not mirrored, so the next run retries it
    instead of caching the failure.
    """
    if cache is not None:
        mirrored = cache.get(url)
        if mirrored is not None:
            return mirrored
    try:
        page = get_text(client, url, label=url)
    except httpx.HTTPError as exc:
        log.warning("skipping %s: %s", url, exc)
        return None
    if cache is not None:
        cache.put(url, page)
    time.sleep(REQUEST_DELAY_S)
    return page


__all__ = ["BASE_URL", "REQUEST_DELAY_S", "fetch_index", "fetch_page", "make_client"]
