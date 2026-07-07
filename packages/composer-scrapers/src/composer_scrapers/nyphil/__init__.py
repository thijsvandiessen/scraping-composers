"""New York Philharmonic performance history (Kaggle dataset nyphil/perf-history).

One kagglehub-cached download (see ``data``), parsed into two record types:

1. Per-(role, name) ``person`` records aggregating each composer/conductor/
   soloist's appearances (see ``people``).
2. One work mention per titled work at each concert (see ``performances``),
   carrying its composer and title for the resolution pipeline (with the
   concert's date/location/soloists kept in ``raw``).

``text`` holds the name cleanup both share.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, WorkMentionDocument
from .data import BASE_URL, _load_programs
from .people import ROLES, _aggregate, _record
from .performances import _performances

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NyPhilAdapter", "ROLES"]


class NyPhilAdapter(SourceAdapter):
    name = "nyphil"
    base_url = BASE_URL
    cadence = RefreshCadence.STATIC

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield every composer/conductor/soloist in the performance history (one
        aggregated ``person`` record each) followed by every work-performance (one
        work mention each). The whole source is one (kagglehub-cached) download;
        ``max_pages`` is accepted for interface compatibility and ignored."""
        ingested_at = datetime.now(UTC)
        programs = _load_programs()
        log.info("nyphil: %d programs", len(programs))
        people = _aggregate(programs)
        for role in ROLES:
            names = sorted(name for r, name in people if r == role)
            log.info("nyphil %s: %d records", role, len(names))
            for name in names:
                record = _record(role, name, people[(role, name)])
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

        count = 0
        for mention in _performances(programs):
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
        log.info("nyphil performances: %d records", count)
