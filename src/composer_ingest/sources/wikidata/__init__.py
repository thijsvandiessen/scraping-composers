"""Wikidata SPARQL client.

Fetches every item with occupation "composer" (Q36834) from the Wikidata Query
Service. ``query`` holds the SPARQL access (paged item query + per-page metrics
query); ``parse`` folds the result rows into one entity document per composer
and formats birth/death dates to their recorded precision.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from ...document import Document
from ...scraper import Scraper, SourceConfig
from .parse import BASE_URL, _records_from_rows
from .query import PAGE_SIZE, REQUEST_DELAY_S, _fetch_metrics, _fetch_page

NAME = "wikidata"

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NAME", "SCRAPER"]

# one page: its result rows plus the popularity metrics for the page's items
_Page = tuple[list[dict[str, Any]], dict[str, dict[str, str]]]


def pages(client: httpx.Client, max_pages: int | None = None) -> Iterator[_Page]:
    """Yield each page (rows + metrics), paging until the query is exhausted."""
    offset = 0
    seen = 0
    while True:
        rows = _fetch_page(client, offset)
        page_qids = sorted({row["item"]["value"].rsplit("/", 1)[-1] for row in rows})
        metrics = _fetch_metrics(client, page_qids) if page_qids else {}
        seen += 1
        log.info("wikidata page %d: %d items (offset=%d)", seen, len(page_qids), offset)
        yield rows, metrics

        # rows aggregate to one record per item in the subquery page, so
        # fewer than PAGE_SIZE items (incl. skipped ones) means last page
        if len(page_qids) < PAGE_SIZE:
            break
        if max_pages is not None and seen >= max_pages:
            log.info("stopping after max_pages=%d", max_pages)
            break
        offset += PAGE_SIZE
        time.sleep(REQUEST_DELAY_S)


def parse(page: _Page) -> Iterator[Document]:
    """One entity document per composer on the page."""
    rows, metrics = page
    yield from _records_from_rows(rows, metrics)


SCRAPER = Scraper(SourceConfig(name=NAME, base_url=BASE_URL, timeout=90.0), pages, parse)
