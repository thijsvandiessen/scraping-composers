"""HTTP access to boosey.com.

Every URL this source knows about is constructed here. The catalogue is walked
in three hops: composer index -> each composer's catalogue search -> each work's
detail page.

The index is one page per letter, and links each composer either to their own
page or straight to their catalogue search. Either way what the walk needs is
the composer id, because a composer page only features a few works. Search
results are loaded by the page's JavaScript and the search itself is held in the
server session: requesting ``powersearch_results`` starts it (and sets the
session cookie), and ``ps_items.cshtml`` then serves its pages. So one session
walks one search at a time, which the client's cookie jar takes care of.

The index and searches are enumerators and stay live; work detail pages are
mirrored. Catalogue URLs redirect (a retitled work's slug 301s to its current
one), so the adapter's client follows redirects.
"""

from __future__ import annotations

import logging
import string
from collections.abc import Iterator

from composer_http import SourceSession

from .catalogue import WorkLink, composer_ids, composer_paths, next_page_path, work_links

BASE_URL = "https://www.boosey.com"

COMPOSER_INDEX_PATH = "/pages/cr/composer/?l1={letter}"
SEARCH_PATH = "/cr/catalogue/ps/powersearch_results?composerid={composer_id}"
SEARCH_ITEMS_PATH = "/cr/catalogue/ps/ps_items.cshtml"

REQUEST_DELAY_S = 0.5
RETRIES = 3
#: Guard against a search whose "next" link cycles back on itself.
MAX_LIST_PAGES = 100

log = logging.getLogger(__name__)


def _absolute(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return BASE_URL + path if path.startswith("/") else f"{BASE_URL}/{path}"


def iter_composer_ids(session: SourceSession) -> Iterator[str]:
    """Every composer id in the catalogue index, letter by letter.

    Raises when the index lists no one: a redesign must fail the run, not
    complete it with zero records.
    """
    seen: set[str] = set()
    for letter in string.ascii_uppercase:
        html = session.get_text(
            _absolute(COMPOSER_INDEX_PATH.format(letter=letter)), label=f"index {letter}", retries=RETRIES
        )
        ids = composer_ids(html)
        for path in composer_paths(html):
            page = session.try_get_text(_absolute(path), label=path, retries=RETRIES)
            on_page = composer_ids(page) if page is not None else []
            if on_page:
                ids.append(on_page[0])
            else:
                log.warning("boosey: no composer id found on %s", path)
        for composer_id in ids:
            if composer_id not in seen:
                seen.add(composer_id)
                yield composer_id
    if not seen:
        raise RuntimeError("boosey: the composer index lists no composers")
    log.info("boosey: %d composers in the index", len(seen))


def composer_work_links(session: SourceSession, composer_id: str) -> list[WorkLink]:
    """Every work in one composer's catalogue search, deduplicated by work id."""
    label = f"search {composer_id}"
    session.get_text(_absolute(SEARCH_PATH.format(composer_id=composer_id)), label=label, retries=RETRIES)
    links: list[WorkLink] = []
    seen: set[str] = set()
    path: str | None = SEARCH_ITEMS_PATH
    requested: set[str] = set()
    while path is not None and path not in requested and len(requested) < MAX_LIST_PAGES:
        requested.add(path)
        html = session.get_text(_absolute(path), label=f"{label} page {len(requested)}", retries=RETRIES)
        for link in work_links(html):
            if link.work_id not in seen:
                seen.add(link.work_id)
                links.append(link)
        path = next_page_path(html)
    return links


def iter_work_pages(
    session: SourceSession, max_pages: int | None = None
) -> Iterator[tuple[WorkLink, str, str]]:
    """Walk the catalogue, yielding ``(link, url, html)`` per work detail page.

    ``max_pages`` caps the number of *detail* fetches, which is what a test run
    wants to bound; the composers are read only as far as that needs. A work
    page that fails after its retries is logged and skipped.
    """
    fetched = 0
    seen: set[str] = set()
    for composer_id in iter_composer_ids(session):
        for link in composer_work_links(session, composer_id):
            if link.work_id in seen:
                continue
            seen.add(link.work_id)
            if max_pages is not None and fetched >= max_pages:
                log.info("boosey: stopping after max_pages=%d work pages", max_pages)
                return
            url = _absolute(link.path)
            fetched += 1
            page = session.try_get_text(url, label=f"work {link.work_id}", retries=RETRIES, mirror=True)
            if page is not None:
                yield link, url, page
    log.info("boosey: fetched %d work pages", fetched)
