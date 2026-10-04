"""IMSLP people API client.

People (type=1) come back with their name embedded in a MediaWiki category
title ("Category:Beethoven, Ludwig van") and an empty ``intvals``; this package
hides the API's quirks (see ``fetch``) and yields clean EntityDocuments. IMSLP's
people list does not distinguish composers from performers/editors/ensembles,
so records carry only the name, no claims.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_http import SourceSession

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence
from .fetch import BASE_URL, PAGE_SIZE, REQUEST_DELAY_S, worklist_page

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ImslpAdapter"]


class ImslpAdapter(HttpSourceAdapter[EntityDocument]):
    name = "imslp"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every person listed on IMSLP, paging until the API is exhausted."""
        start = 0
        pages = 0
        while True:
            data = worklist_page(session, start)
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
