"""Composer catalogue of classical-music-online.net.

The site publishes an A-Z index of ~11.6k composers, each with a page listing
that composer's works — so unlike the concert archives, its work mentions are a
catalogue rather than a programme. Two document kinds come out of one crawl:

1. one ``person`` record per composer, with life years and country from the
   index row (see ``composers``);
2. one work mention per catalogued work, title verbatim and opus in ``raw``
   (see ``works`` for why the opus is deliberately kept out of the title).

The crawl is deliberately slow (a delay between every request, ~11.6k composer
pages), so the cadence is yearly and a run is meant to be left unattended;
re-processing the resulting snapshot needs no network at all.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, WorkMentionDocument
from .composers import index_record
from .fetch import BASE_URL, iter_composers
from .works import iter_work_mentions

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ClassicalMusicOnlineAdapter"]


class ClassicalMusicOnlineAdapter(SourceAdapter):
    name = "classicalmusiconline"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield each composer's person record followed by its work mentions.

        ``max_pages`` caps the number of composer pages fetched, for test runs.
        """
        ingested_at = datetime.now(UTC)
        composers = 0
        works = 0
        for entry, page in iter_composers(max_pages=max_pages):
            record = index_record(entry)
            composers += 1
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
            for mention in iter_work_mentions(page, entry.name, entry.external_id, BASE_URL):
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
        log.info("classicalmusiconline: %d composers, %d work mentions", composers, works)
