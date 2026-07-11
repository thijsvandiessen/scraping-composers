"""Open Opus work catalogue (openopus.org).

Open Opus is a curated open database of "essential" classical composers and
their works, published whole as one JSON dump (see ``fetch``). Each composer
yields two kinds of record:

1. one ``person`` record with birth/death years and the composer's epoch as
   an ``associated_period`` claim — Open Opus tracks years only, padded to
   January 1st, so the padding is stripped before the year is stored;
2. one work mention per catalogued work, carrying the composer's full name
   and the work title for the resolution pipeline (subtitle, genre, and
   popularity flags kept in ``raw``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .fetch import BASE_URL, _fetch_dump, _make_client

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "OpenOpusAdapter"]


def _year(date: object) -> str | None:
    """Strip Open Opus's January-1st padding ("1685-01-01" -> "1685") so the
    stored value never overstates how precisely the date is known; a genuine
    day-precision date, should one ever appear, passes through untouched."""
    if not date or not isinstance(date, str):
        return None
    return date.removesuffix("-01-01") or None


class OpenOpusAdapter(SourceAdapter):
    name = "openopus"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield one ``person`` record per composer in the dump, followed by
        one work mention per catalogued work. ``max_pages`` caps the number of
        composers processed, for test runs."""
        with _make_client() as client:
            composers = _fetch_dump(client)
        ingested_at = datetime.now(UTC)
        seen = 0
        mentions = 0
        for composer in composers:
            name = composer.get("complete_name") or composer.get("name") or ""
            composer_id = str(composer.get("id") or "").strip()
            if not name or not composer_id:
                log.debug("skipping composer without name/id: %r", composer)
                continue
            if max_pages is not None and seen >= max_pages:
                break
            seen += 1

            claims = [SourceClaim("has_profession", "profession", "composer")]
            born = _year(composer.get("birth"))
            if born:
                claims.append(SourceClaim("born_on", value=born))
            died = _year(composer.get("death"))
            if died:
                claims.append(SourceClaim("died_on", value=died))
            epoch = composer.get("epoch")
            if epoch:
                claims.append(SourceClaim("associated_period", "period", epoch))
            # works are yielded as their own documents; repeating the full
            # catalogue inside every composer's raw payload would only bloat it
            raw = {key: value for key, value in composer.items() if key != "works"}
            yield EntityDocument(
                id=f"composer:{composer_id}",
                url=BASE_URL,
                source_name=self.name,
                ingested_at=ingested_at,
                name=name,
                kind="person",
                raw=raw,
                claims=tuple(claims),
            )

            for work in composer.get("works") or ():
                title = work.get("title") or ""
                work_id = str(work.get("id") or "").strip()
                if not title or not work_id:
                    log.debug("skipping work without title/id for composer %s: %r", composer_id, work)
                    continue
                mentions += 1
                yield WorkMentionDocument(
                    id=f"work:{work_id}",
                    url=BASE_URL,
                    source_name=self.name,
                    ingested_at=ingested_at,
                    title=title,
                    composer=name,
                    raw={**work, "composer_id": composer_id, "epoch": composer.get("epoch")},
                )
        log.info("openopus: %d composers, %d work mentions", seen, mentions)
