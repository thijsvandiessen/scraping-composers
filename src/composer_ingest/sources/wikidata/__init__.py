"""Wikidata SPARQL client.

Fetches every item with occupation "composer" (Q36834) from the Wikidata Query
Service. ``query`` holds the SPARQL access (paged item query + per-page metrics
query); ``parse`` folds the result rows into one SourceRecord per composer and
formats birth/death dates to their recorded precision.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import httpx

from .. import SourceRecord
from .parse import BASE_URL, _records_from_rows
from .query import PAGE_SIZE, REQUEST_DELAY_S, _fetch_metrics, _fetch_page

NAME = "wikidata"

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NAME", "fetch_records"]


def fetch_records(max_pages: int | None = None) -> Iterator[SourceRecord]:
    """Yield every composer on Wikidata, paging until the query is exhausted."""
    offset = 0
    pages = 0
    with httpx.Client(
        headers={"User-Agent": "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"},
        timeout=90,  # WDQS may take up to its 60s execution limit
    ) as client:
        while True:
            rows = _fetch_page(client, offset)
            page_qids = sorted({row["item"]["value"].rsplit("/", 1)[-1] for row in rows})
            metrics = _fetch_metrics(client, page_qids) if page_qids else {}
            records = _records_from_rows(rows, metrics)
            pages += 1
            log.info("wikidata page %d: %d composers (offset=%d)", pages, len(records), offset)
            yield from records

            # rows aggregate to one record per item in the subquery page, so
            # fewer than PAGE_SIZE items (incl. skipped ones) means last page
            if len(page_qids) < PAGE_SIZE:
                break
            if max_pages is not None and pages >= max_pages:
                log.info("stopping after max_pages=%d", max_pages)
                break
            offset += PAGE_SIZE
            time.sleep(REQUEST_DELAY_S)
