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

from .. import EntityDocument, SourceAdapter, WorkMentionDocument
from .dropdowns import SELECTS, _options, _record
from .fetch import BASE_URL, _fetch_list_page, _fetch_search_page
from .performances import _performances

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ConcertgebouwAdapter"]


class ConcertgebouwAdapter(SourceAdapter):
    name = "concertgebouw"
    base_url = BASE_URL

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield every composer/conductor/soloist in the archive's search filters
        (one ``person`` record each) followed by every work-performance in the List
        view (one work mention each). The whole source is two fetches;
        ``max_pages`` is accepted for interface compatibility and ignored."""
        ingested_at = datetime.now(UTC)
        page = _fetch_search_page()
        for select_id, profession in SELECTS:
            count = 0
            for value, label in _options(page, select_id):
                record = _record(select_id, profession, value, label)
                if record is not None:
                    count += 1
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
            log.info("concertgebouw %s: %d records", select_id, count)

        count = 0
        for mention in _performances(_fetch_list_page()):
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
