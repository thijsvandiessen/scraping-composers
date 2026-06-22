"""IMSLP people API client.

People (type=1) come back with their name embedded in a MediaWiki category
title ("Category:Beethoven, Ludwig van") and an empty ``intvals``; this package
hides the API's quirks (see ``fetch``) and yields clean entity documents.
IMSLP's people list does not distinguish composers from performers/editors/
ensembles, so documents carry only the name, no claims.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from ...document import Document, entity_document
from ...scraper import Scraper, SourceConfig
from .fetch import BASE_URL, PAGE_SIZE, REQUEST_DELAY_S, _fetch_page

NAME = "imslp"

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NAME", "SCRAPER"]


def pages(client: httpx.Client, max_pages: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield each API page, paging until the API is exhausted."""
    start = 0
    seen = 0
    while True:
        data = _fetch_page(client, start)
        meta = data.pop("metadata", {})
        seen += 1
        log.info("imslp page %d: %d records (start=%d)", seen, len(data), start)
        yield data

        if not meta.get("moreresultsavailable"):
            break
        if max_pages is not None and seen >= max_pages:
            log.info("stopping after max_pages=%d", max_pages)
            break
        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY_S)


def parse(data: dict[str, Any]) -> Iterator[Document]:
    """One entity document per person on the page (name only, no claims)."""
    # Rows are keyed "0", "1", ... — sort numerically to keep API order.
    for key in sorted(data, key=int):
        row = data[key]
        category_id = row.get("id", "")
        name = category_id.removeprefix("Category:").strip()
        if not name:
            continue
        yield entity_document(id=category_id, name=name, url=row.get("permlink"), raw=row)


SCRAPER = Scraper(SourceConfig(name=NAME, base_url=BASE_URL), pages, parse)
