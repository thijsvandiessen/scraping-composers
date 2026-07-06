"""Royal Concertgebouw Orchestra live calendar (concertgebouworkest.nl).

Three data feeds in one adapter:

1. The conductors overview page yields one rich ``person`` record per conductor,
   with biography, function label ("chief conductor 2016-2018"), stable credit ID,
   portrait image and profile URL.
2. The HTML calendar listing (paginated) discovers concert slugs; each slug is
   resolved to a structured JSON detail page with programme and credits.
3. From each concert detail: one work mention per non-interval programme item
   (with instrumentation and the concert URL for source attribution), plus one
   person record per unique credited artist (conductor + soloists).

Conductor records from feeds #1 and #2 merge automatically via the dedup key
(name) in the ingestion pipeline, with feed #1 contributing the richer profile.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, WorkMentionDocument
from .artists import _Credit, collect_credits, credit_record, iter_conductor_records
from .fetch import BASE_URL, fetch_conductors, iter_concerts
from .performances import _performances

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "RcoAdapter"]


class RcoAdapter(SourceAdapter):
    name = "rco"
    base_url = BASE_URL
    cadence = RefreshCadence.WEEKLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield conductor profiles, then work mentions + credits from the calendar.

        Pass 1: fetch the conductors page; yield one EntityDocument per conductor
        (rich profile with biography, function label and profile URL).
        Pass 2: paginate the calendar and fetch each concert detail; yield a
        WorkMentionDocument per non-interval programme item as they arrive, while
        accumulating a registry of unique credited persons.
        Pass 3: yield one EntityDocument per unique credited person (conductors
        and soloists from all concerts).

        ``max_pages`` caps the number of concert detail fetches for test runs.
        """
        ingested_at = datetime.now(UTC)

        # Pass 1: conductor profiles
        conductors_page = fetch_conductors()
        conductor_records = iter_conductor_records(conductors_page)
        log.info("rco conductors: %d records", len(conductor_records))
        for record in conductor_records:
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

        # Pass 2: concert credits + work mentions
        credit_registry: dict[str, _Credit] = {}
        works = 0
        for concert in iter_concerts(max_pages=max_pages):
            collect_credits(concert, credit_registry)
            for mention in _performances(concert):
                works += 1
                yield WorkMentionDocument(
                    id=mention.external_id,
                    url=mention.raw.get("url"),
                    source_name=self.name,
                    ingested_at=ingested_at,
                    title=mention.title,
                    composer=mention.composer,
                    raw=mention.raw,
                )
        log.info("rco: %d work mentions, %d unique credits", works, len(credit_registry))

        # Pass 3: credit entities
        for credit in credit_registry.values():
            record = credit_record(credit)
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
