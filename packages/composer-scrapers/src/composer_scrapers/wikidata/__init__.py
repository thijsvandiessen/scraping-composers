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
from datetime import UTC, datetime

from composer_http import new_client

from .. import EntityDocument, RefreshCadence, SourceAdapter
from .parse import BASE_URL, _records_from_rows
from .query import PAGE_SIZE, REQUEST_DELAY_S, _fetch_metrics, _fetch_page

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "WikidataAdapter"]


class WikidataAdapter(SourceAdapter):
    name = "wikidata"
    base_url = BASE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every composer on Wikidata, paging until the query is exhausted."""
        after: str | None = None
        pages = 0
        with new_client(timeout=90) as client:
            while True:
                rows = _fetch_page(client, after)
                page_qids = sorted({row["item"]["value"].rsplit("/", 1)[-1] for row in rows})
                metrics = _fetch_metrics(client, page_qids) if page_qids else {}
                records = _records_from_rows(rows, metrics)
                ingested_at = datetime.now(UTC)
                pages += 1
                log.info("wikidata page %d: %d composers (after=%s)", pages, len(records), after or "START")
                for record in records:
                    yield EntityDocument(
                        id=record.external_id,
                        url=record.url,
                        source_name=self.name,
                        ingested_at=ingested_at,
                        name=record.name,
                        kind=record.kind,
                        raw=record.raw,
                        claims=record.claims,
                    )

                if len(page_qids) < PAGE_SIZE:
                    break
                if max_pages is not None and pages >= max_pages:
                    log.info("stopping after max_pages=%d", max_pages)
                    break
                # seek from the last (max) QID on this page — page_qids is sorted,
                # so page_qids[-1] is the keyset cursor for the next range scan
                after = page_qids[-1]
                time.sleep(REQUEST_DELAY_S)
