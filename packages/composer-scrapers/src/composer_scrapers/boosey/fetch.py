"""HTTP access to boosey.com.

Every URL this source knows about is constructed here, so pointing the adapter
at the real site is a one-file change. The catalogue is walked in three hops:
composer index -> each composer's work list -> each work's detail page.

The only URL shape confirmed from a live page is the work detail path
(``/cr/music/<slug>/<id>``); ``COMPOSER_INDEX_PATH`` below is the entry point to
check first if a run discovers no composers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import httpx

from .._http import call_with_retries, user_agent
from .catalogue import WorkLink, composer_paths, next_page_path, work_links

BASE_URL = "https://www.boosey.com"

#: Entry point for discovery. Verify against the live site before a full run.
COMPOSER_INDEX_PATH = "/composers"

REQUEST_DELAY_S = 0.5
RETRIES = 3
#: Guard against a listing whose "next" link cycles back on itself.
MAX_LIST_PAGES = 100

log = logging.getLogger(__name__)


def _make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent()}, timeout=30, follow_redirects=True)


def _get_text(client: httpx.Client, url: str, label: str) -> str:
    def do() -> str:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text

    return call_with_retries(do, label=label, retries=RETRIES)


def _absolute(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return BASE_URL + path if path.startswith("/") else f"{BASE_URL}/{path}"


def _listing_pages(client: httpx.Client, path: str, label: str) -> Iterator[str]:
    """Yield the HTML of a listing page and each ``rel="next"`` page after it."""
    seen: set[str] = set()
    for page in range(MAX_LIST_PAGES):
        url = _absolute(path)
        if url in seen:
            return
        seen.add(url)
        if page:
            time.sleep(REQUEST_DELAY_S)
        html = _get_text(client, url, f"{label} page {page + 1}")
        yield html
        following = next_page_path(html)
        if following is None:
            return
        path = following


def composer_index(client: httpx.Client) -> list[str]:
    """Every composer path in the catalogue index."""
    paths: list[str] = []
    seen: set[str] = set()
    for html in _listing_pages(client, COMPOSER_INDEX_PATH, "composer index"):
        for path in composer_paths(html):
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def composer_work_links(client: httpx.Client, composer_path: str) -> list[WorkLink]:
    """Every work link on one composer's pages, deduplicated by work id."""
    links: list[WorkLink] = []
    seen: set[str] = set()
    for html in _listing_pages(client, composer_path, f"works for {composer_path}"):
        for link in work_links(html):
            if link.work_id not in seen:
                seen.add(link.work_id)
                links.append(link)
    return links


def iter_work_pages(max_pages: int | None = None) -> Iterator[tuple[WorkLink, str, str]]:
    """Walk the catalogue, yielding ``(link, url, html)`` per work detail page.

    ``max_pages`` caps the number of *detail* fetches, which is what a test run
    wants to bound; index and listing pages are cheap by comparison.
    """
    with _make_client() as client:
        composers = composer_index(client)
        log.info("boosey: %d composers in the index", len(composers))
        fetched = 0
        seen: set[str] = set()
        for composer_path in composers:
            for link in composer_work_links(client, composer_path):
                if link.work_id in seen:
                    continue
                seen.add(link.work_id)
                if max_pages is not None and fetched >= max_pages:
                    log.info("boosey: stopping after max_pages=%d work pages", max_pages)
                    return
                url = _absolute(link.path)
                time.sleep(REQUEST_DELAY_S)
                yield link, url, _get_text(client, url, f"work {link.work_id}")
                fetched += 1
        log.info("boosey: fetched %d work pages", fetched)
