"""HTTP access to the IMSLP work catalogue.

This source used to discover works by walking IMSLP the way a reader does:
take a composer from gold, guess their category URL, verify it, split the page
into "Compositions" and "Collected Works" sections, follow "next 200" links,
and fetch each work page found. That was three kinds of HTML parsing before a
single work was read, it only ever saw composers gold already knew, and it
produced 7,256 works.

None of it was necessary. The same ``API.ISCR.php`` endpoint
:mod:`composer_scrapers.imslp` reads for people serves the **whole work
catalogue** under ``type=2`` — around 267,000 works, a thousand per request,
each already carrying its composer, title, IMSLP catalogue number and page id:

    {"id": "Jolie Pas (Rawlings, Charles Arthur)", "type": "2",
     "intvals": {"composer": "Rawlings, Charles Arthur", "worktitle": "Jolie Pas",
                 "icatno": "ICR 6", "pageid": "469997"},
     "permlink": "https://imslp.org/wiki/Jolie_Pas_(Rawlings,_Charles_Arthur)"}

So the catalogue is ~267 requests rather than a crawl, no composer needs
resolving, and nothing is scoped to gold. Every work IMSLP lists is read.

Those requests are not fast — the endpoint takes 5 to 8 seconds each, and more
at higher offsets, so the listing alone is around half an hour. That is still
the cheap half.

The **detail pages are the expensive half** and stay a second pass over that
spine: one request per work, so a full enrichment sweep is tens of hours.
``max_details`` bounds it, and every work is still yielded — a run that
enriches nothing still reports the entire catalogue.

Detail pages are read through ``api.php`` rather than ``/wiki/<Title>``, which
is worth doing for two reasons beyond the 6.2KB-against-13.8KB payload:
:mod:`composer_scrapers.imslp_recordings` requests those same pages at the same
URL, so the two sources share one :class:`~composer_http.PageCache` mirror and
the second sweep to run gets its overlap free; and the worklist hands over
``pageid``, so a title never has to survive a round trip through a URL.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
from composer_http import PageCache, get_text, new_client, open_page_cache

from ..imslp.fetch import PAGE_SIZE, WORKS, worklist_page

log = logging.getLogger(__name__)

BASE_URL = "https://imslp.org"
API_URL = BASE_URL + "/api.php"

#: Between uncached detail requests. Matches the delay
#: :mod:`composer_scrapers.imslp_recordings` uses against the same endpoint.
REQUEST_DELAY_S = 1.0

RETRIES = 3


@dataclass(frozen=True)
class WorkRow:
    """One work as the bulk worklist states it, before any page is fetched."""

    page_id: int
    title: str
    composer: str
    catalogue_number: str | None
    url: str


def parse_url(page_id: int) -> str:
    """The parser output for one work page.

    Deliberately byte-identical to
    :func:`composer_scrapers.imslp_recordings.fetch.parse_url`: the page mirror
    is keyed by URL, so the two IMSLP sources share their overlap only as long
    as they ask for pages the same way.
    """
    return f"{API_URL}?action=parse&pageid={page_id}&prop=text&format=json"


def _row(entry: Any) -> WorkRow | None:
    """One worklist row, or None when it is missing what identifies a work."""
    if not isinstance(entry, dict):
        return None
    values = entry.get("intvals")
    if not isinstance(values, dict):
        return None
    page_id = values.get("pageid")
    composer = values.get("composer")
    title = entry.get("id")
    if not (page_id and isinstance(composer, str) and isinstance(title, str)):
        return None
    try:
        page_id = int(page_id)
    except (TypeError, ValueError):
        return None
    permlink = entry.get("permlink")
    return WorkRow(
        page_id=page_id,
        title=title,
        composer=composer,
        # The worklist writes "no catalogue number" as an empty string.
        catalogue_number=_text(values.get("icatno")),
        url=permlink if isinstance(permlink, str) else f"{BASE_URL}/wiki/{page_id}",
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def iter_worklist(client: httpx.Client) -> Iterator[WorkRow]:
    """Every work IMSLP lists, paging the bulk endpoint until it is exhausted."""
    start = 0
    seen = 0
    while True:
        data = worklist_page(client, start, WORKS)
        meta = data.pop("metadata", {})
        for key in sorted(data, key=int):
            row = _row(data[key])
            if row is not None:
                seen += 1
                yield row
        if not meta.get("moreresultsavailable"):
            log.info("imslp_works: %d works in the catalogue", seen)
            return
        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY_S)


def iter_works(max_details: int | None = None) -> Iterator[tuple[WorkRow, str | None]]:
    """The catalogue, as ``(row, parser html or None)`` per work.

    ``max_details`` caps how many work pages are *fetched*; every work is
    yielded either way, with ``None`` for the ones left unenriched. Fetches are
    counted as attempts rather than successes, so a run of failing pages cannot
    silently outgrow its request budget.
    """
    cache = open_page_cache()
    enriched = 0
    attempted = 0
    with new_client() as client:
        for row in iter_worklist(client):
            document: str | None = None
            if max_details is None or attempted < max_details:
                attempted += 1
                document = _detail(client, cache, row)
                enriched += document is not None
            yield row, document
    log.info(
        "imslp_works: %d/%d detail pages read; page mirror: %s",
        enriched,
        attempted,
        cache.summary() if cache is not None else "off",
    )


def _detail(client: httpx.Client, cache: PageCache | None, row: WorkRow) -> str | None:
    """The parser output for one work page, from the mirror or the API."""
    url = parse_url(row.page_id)
    mirrored = cache.get(url) if cache is not None else None
    if mirrored is not None:
        return _document(mirrored, row.title)
    time.sleep(REQUEST_DELAY_S)
    try:
        raw = get_text(client, url, label=f"work {row.page_id} ({row.title})", retries=RETRIES)
    except httpx.HTTPError as exc:
        # One work must not abort a sweep measured in hours — IMSLP has answered
        # a detail request with a bot-check interstitial before now.
        log.warning("imslp_works: skipping %r after error (%s)", row.title, exc)
        return None
    document = _document(raw, row.title)
    # Only a usable answer is mirrored: MediaWiki 1.18 reports an unknown page
    # as a 200 carrying an error object, and caching that would mean never
    # retrying a page that failed once.
    if document is not None and cache is not None:
        cache.put(url, raw)
    return document


def _document(raw: str, title: str) -> str | None:
    """The parser output inside one API response body, or None."""
    try:
        body = json.loads(raw)
    except ValueError as exc:
        log.warning("imslp_works: unreadable API response for %r (%s)", title, exc)
        return None
    parse = body.get("parse") if isinstance(body, dict) else None
    if not isinstance(parse, dict):
        error = body.get("error") if isinstance(body, dict) else None
        log.warning("imslp_works: no parse output for %r (%s)", title, error)
        return None
    text = parse.get("text")
    document = text.get("*") if isinstance(text, dict) else None
    return document if isinstance(document, str) else None
