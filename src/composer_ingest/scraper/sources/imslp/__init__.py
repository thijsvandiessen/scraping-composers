"""IMSLP people API client.

People (type=1) come back with their name embedded in a MediaWiki category
title ("Category:Beethoven, Ludwig van") and an empty ``intvals``; this package
hides the API's quirks (see ``fetch``) and yields clean EntityDocuments. IMSLP's
people list does not distinguish composers from performers/editors/ensembles,
so records carry only the name, no claims.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from .. import EntityDocument, RefreshCadence, SourceAdapter
from .fetch import BASE_URL, PAGE_SIZE, REQUEST_DELAY_S, _fetch_page

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ImslpAdapter"]


class ImslpAdapter(SourceAdapter):
    name = "imslp"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every person listed on IMSLP, paging until the API is exhausted."""
        start = 0
        pages = 0
        with httpx.Client(
            headers={"User-Agent": "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"},
            timeout=30,
        ) as client:
            while True:
                data = _fetch_page(client, start)
                meta = data.pop("metadata", {})
                pages += 1
                log.info("imslp page %d: %d records (start=%d)", pages, len(data), start)

                ingested_at = datetime.now(UTC)
                for key in sorted(data, key=int):
                    row = data[key]
                    category_id = row.get("id", "")
                    name = category_id.removeprefix("Category:").strip()
                    if not name:
                        continue
                    yield EntityDocument(
                        id=category_id,
                        url=row.get("permlink"),
                        source_name=self.name,
                        ingested_at=ingested_at,
                        name=name,
                        raw=row,
                    )

                if not meta.get("moreresultsavailable"):
                    break
                if max_pages is not None and pages >= max_pages:
                    log.info("stopping after max_pages=%d", max_pages)
                    break
                start += PAGE_SIZE
                time.sleep(REQUEST_DELAY_S)
