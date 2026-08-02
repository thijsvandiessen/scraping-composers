"""Boosey & Hawkes work catalogue (boosey.com).

The publisher's own catalogue, and the first source here that carries *work*
metadata — scoring, duration, year of composition — rather than concert
programmes or people lists.

Each work yields two documents sharing the work's Boosey id, because the
warehouse stores those two things in different places (see
``composer_warehouse.ingestion.core``):

* a :class:`~composer_schema.WorkMentionDocument`, which the work matcher
  resolves to a canonical ``works`` row (with the full metadata kept in ``raw``);
* an :class:`~composer_schema.EntityDocument` of kind ``work``, whose
  :class:`~composer_schema.SourceClaim` s make the scoring and duration
  queryable alongside every other claim.

The entity's label is composer-qualified ("Kerori (Walter Steffens)") on purpose:
entity dedup keys on the normalised label alone, so a bare title would merge
two composers' identically-named works into one entity and pool their claims.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .catalogue import WorkLink
from .fetch import BASE_URL, iter_work_pages
from .works import ParsedWork, duration_minutes, parse_work

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "BooseyAdapter"]


def _entity_label(work: ParsedWork) -> str:
    """Composer-qualified work label; see the module docstring for why."""
    if work.composer:
        return f"{work.title} ({work.composer})"
    return work.title


def _claims(work: ParsedWork) -> tuple[SourceClaim, ...]:
    """Typed assertions about the work. Values stay verbatim except duration,
    which is normalised to whole minutes so it can be compared across sources."""
    claims: list[SourceClaim] = []
    if work.composer:
        claims.append(SourceClaim("composed_by", object_kind="person", object_label=work.composer))
    if publisher := work.fields.get("publisher"):
        claims.append(SourceClaim("published_by", object_kind="publisher", object_label=publisher))
    if scoring := work.fields.get("scoring"):
        claims.append(SourceClaim("has_scoring", value=scoring))
    if minutes := duration_minutes(work.fields.get("duration")):
        claims.append(SourceClaim("has_duration", value=str(minutes)))
    if year := work.fields.get("year"):
        claims.append(SourceClaim("composed_in", value=year))
    return tuple(claims)


def _raw(work: ParsedWork, link: WorkLink, url: str) -> dict[str, object]:
    """Everything the page stated, kept verbatim so a later pass can structure
    fields this adapter does not yet turn into claims."""
    return {
        "work_id": link.work_id,
        "url": url,
        "title": work.title,
        "composer": work.composer,
        "duration_minutes": duration_minutes(work.fields.get("duration")),
        **work.fields,
    }


class BooseyAdapter(SourceAdapter):
    name = "boosey"
    base_url = BASE_URL
    # A publisher's back catalogue changes slowly: new works appear, existing
    # entries rarely move.
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Walk the catalogue, yielding a work mention and a work entity per work.

        ``max_pages`` caps the number of work detail pages fetched.
        """
        ingested_at = datetime.now(UTC)
        works = 0
        skipped = 0
        for link, url, html in iter_work_pages(max_pages=max_pages):
            work = parse_work(html)
            if work is None:
                skipped += 1
                log.debug("boosey: no title on %s, skipping", url)
                continue
            works += 1
            raw = _raw(work, link, url)
            yield WorkMentionDocument(
                id=link.work_id,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                title=work.title,
                composer=work.composer,
                raw=raw,
            )
            yield EntityDocument(
                id=link.work_id,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                name=_entity_label(work),
                kind="work",
                raw=raw,
                claims=_claims(work),
            )
        log.info("boosey: %d works, %d pages skipped", works, skipped)
