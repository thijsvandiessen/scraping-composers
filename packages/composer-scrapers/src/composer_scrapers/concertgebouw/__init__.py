"""Concertgebouworkest concert archive (archief.concertgebouworkest.nl).

Two views of the archive, both fetched in one request each (see ``fetch`` for
the HTTP, ``dropdowns`` for the per-person filter lists, and ``performances``
for the List view of every work performed):

1. The search form's filter dropdowns yield one ``person`` record each, with
   the profession and (composers) birth/death years and (soloists) discipline.
2. The "List" view yields one ``work`` record per work-performance, linking it
   to its composer/conductor/soloists/date/city as claims.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_http import SourceSession

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence, WorkMentionDocument
from .dropdowns import SELECTS, options, record
from .fetch import BASE_URL, REQUEST_DELAY_S, fetch_list_page, fetch_search_page
from .performances import performances

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ConcertgebouwAdapter"]


class ConcertgebouwAdapter(HttpSourceAdapter[EntityDocument | WorkMentionDocument]):
    name = "concertgebouw_archive"
    base_url = BASE_URL
    # an archive of concerts already given: scraped once
    cadence = RefreshCadence.STATIC
    request_delay_s = REQUEST_DELAY_S

    def scrape(
        self, session: SourceSession, max_pages: int | None = None
    ) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield every composer/conductor/soloist in the archive's search filters
        (one ``person`` record each) followed by every work-performance in the List
        view (one work mention each). The whole source is two fetches;
        ``max_pages`` is accepted for interface compatibility and ignored."""
        ingested_at = datetime.now(UTC)
        page = fetch_search_page(session)
        list_page = fetch_list_page(session)
        for select_id, profession in SELECTS:
            count = 0
            for value, label in options(page, select_id):
                rec = record(select_id, profession, value, label)
                if rec is not None:
                    count += 1
                    yield EntityDocument(
                        id=rec.external_id,
                        url=rec.url,
                        source_name=self.name,
                        ingested_at=ingested_at,
                        name=rec.name,
                        kind=rec.kind,
                        raw=rec.raw,
                        claims=rec.claims,
                    )
            log.info("concertgebouw %s: %d records", select_id, count)

        count = 0
        for mention in performances(list_page):
            count += 1
            yield WorkMentionDocument(
                id=mention.external_id,
                url=None,
                source_name=self.name,
                ingested_at=ingested_at,
                title=mention.title,
                composer=mention.composer,
                raw=mention.raw,
            )
        log.info("concertgebouw performances: %d records", count)
