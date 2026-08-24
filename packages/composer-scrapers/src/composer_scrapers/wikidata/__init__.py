"""Wikidata SPARQL client.

Fetches every item with occupation "composer" (Q36834) from the Wikidata Query
Service. ``query`` holds the SPARQL access (id-list query + per-batch detail
and metrics queries); ``parse`` folds the result rows into one SourceRecord per
composer and formats birth/death dates to their recorded precision.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_http import new_client

from .. import EntityDocument, RefreshCadence, SourceAdapter
from .parse import BASE_URL, _records_from_rows
from .query import PAGE_SIZE, REQUEST_DELAY_S, _fetch_metrics, _fetch_page, _fetch_qids

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "WikidataAdapter"]


class WikidataAdapter(SourceAdapter):
    name = "wikidata"
    base_url = BASE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every composer on Wikidata.

        The full id list is fetched first, then details are fetched in
        PAGE_SIZE batches bound with VALUES -- see the ``query`` module for why
        the pages are driven from a client-side list rather than a server-side
        cursor. ``max_pages`` caps the number of batches, for test runs; ids
        are numerically ordered, so a capped run still covers the best-known
        composers."""
        with new_client(timeout=90) as client:
            qids = _fetch_qids(client)
            if not qids:
                raise RuntimeError("wikidata returned no composer ids")
            batches = [qids[i : i + PAGE_SIZE] for i in range(0, len(qids), PAGE_SIZE)]
            if max_pages is not None and max_pages < len(batches):
                log.info("stopping after max_pages=%d", max_pages)
                batches = batches[:max_pages]
            log.info("wikidata: %d composers in %d pages", len(qids), len(batches))

            for page, batch in enumerate(batches, start=1):
                if page > 1:
                    time.sleep(REQUEST_DELAY_S)
                rows = _fetch_page(client, batch)
                metrics = _fetch_metrics(client, batch)
                records = _records_from_rows(rows, metrics)
                ingested_at = datetime.now(UTC)
                log.info("wikidata page %d/%d: %d composers", page, len(batches), len(records))
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
