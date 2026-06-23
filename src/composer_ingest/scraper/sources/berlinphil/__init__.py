"""Berliner Philharmoniker Digital Concert Hall archive (digitalconcerthall.com).

The Digital Concert Hall is the orchestra's video archive — a high-quality,
already-normalized subset of its concerts (works, composers, conductors,
soloists, periods) spanning several decades, not its complete performance
history. Backed by an unauthenticated JSON API (see ``fetch``), it yields two
kinds of record in one pass over every archived concert:

1. one work mention per work performed (``performances``), carrying its first
   composer and title for the resolution pipeline (with conductor/soloists,
   orchestra, period and date kept in ``raw``);
2. one ``person`` (or ``ensemble``) record per distinct artist (``artists``),
   accumulated across all concerts, with their professions and (soloists) the
   instruments/voices they played.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, SourceAdapter, WorkMentionDocument
from .artists import _Artist, _artist_record, _collect
from .fetch import BASE_URL, iter_concerts
from .performances import _performances

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "BerlinPhilAdapter"]


class BerlinPhilAdapter(SourceAdapter):
    name = "berlinphil"
    base_url = BASE_URL

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield every work-performance in the archive (one work mention each),
        then one ``person``/``ensemble`` record per distinct artist seen along the
        way. ``max_pages`` caps the number of concerts fetched, for test runs."""
        ingested_at = datetime.now(UTC)
        registry: dict[str, _Artist] = {}
        works = 0
        for concert in iter_concerts(max_pages=max_pages):
            _collect(concert, registry)
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
        log.info("berlinphil: %d work-performances, %d artists", works, len(registry))
        for info in registry.values():
            record = _artist_record(info)
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
