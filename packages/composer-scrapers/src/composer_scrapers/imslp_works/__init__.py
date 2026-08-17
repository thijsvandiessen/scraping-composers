"""IMSLP work catalogue (imslp.org), scoped to gold's own composer list.

Every other source here discovers *who* to scrape from the source itself;
this one is the exception, driven by gold.db instead (see ``.gold``) — the
composer list is curated and comparatively small, so the crawl only walks
IMSLP for people already worth having works for, rather than IMSLP's full
~55k-person catalogue.

Each work yields two documents sharing its IMSLP page title as their id,
because the warehouse stores those two things in different places (see
``composer_warehouse.ingestion.core``), the same split ``boosey`` uses for
its own work catalogue:

* a :class:`~composer_schema.WorkMentionDocument`, which the work matcher
  resolves to a canonical ``works`` row (with everything the page stated
  kept in ``raw``);
* an :class:`~composer_schema.EntityDocument` of kind ``work``, whose
  :class:`~composer_schema.SourceClaim` s make the instrumentation
  (``has_scoring`` — what the user actually asked for) queryable alongside
  every other claim.

The entity's label is the page's own composer-qualified title ("Piano Sonata
No.32, Op.111 (Beethoven, Ludwig van)") rather than the bare work title, for
the same reason as ``boosey``: entity dedup keys on the normalised label
alone, so a bare title would merge two composers' identically-named works
into one entity and pool their claims.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_config import settings

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .fetch import BASE_URL, iter_work_pages
from .gold import GoldComposer
from .works import ParsedWork, parse_work, strip_composer_suffix

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ImslpWorksAdapter"]


def _claims(composer: GoldComposer, work: ParsedWork) -> tuple[SourceClaim, ...]:
    """Typed assertions about the work. Values stay verbatim except where
    ``composer_by``'s object is resolved from gold's own label rather than
    the page's (which can differ by diacritics/name order)."""
    claims: list[SourceClaim] = [
        SourceClaim("composed_by", object_kind="person", object_label=composer.label)
    ]
    if scoring := work.fields.get("instrumentation"):
        claims.append(SourceClaim("has_scoring", value=scoring))
    if year := work.fields.get("composition_year"):
        claims.append(SourceClaim("composed_in", value=year))
    if key := work.fields.get("key"):
        claims.append(SourceClaim("has_key", value=key))
    if genres := work.fields.get("genre_categories"):
        claims.append(SourceClaim("has_genre", value=genres))
    return tuple(claims)


def _raw(work: ParsedWork, path: str, url: str) -> dict[str, object]:
    """Everything the page stated, kept verbatim so a later pass can
    structure fields this adapter does not yet turn into claims."""
    return {"path": path, "url": url, "title": work.title, **work.fields}


class ImslpWorksAdapter(SourceAdapter):
    name = "imslp_works"
    base_url = BASE_URL
    # A wiki catalogue changes slowly, and this crawl is scoped to gold's own
    # composer list rather than IMSLP's full site — not worth re-running often.
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Walk gold's composers against IMSLP, yielding a work mention and a
        work entity per work.

        ``max_pages`` caps the number of work detail pages fetched.
        """
        ingested_at = datetime.now(UTC)
        works = 0
        skipped = 0
        for composer, path, url, html in iter_work_pages(settings.gold_db_path, max_pages=max_pages):
            work = parse_work(html)
            if work is None:
                skipped += 1
                log.debug("imslp_works: no title on %s, skipping", url)
                continue
            works += 1
            stripped_title = strip_composer_suffix(work.title, composer.label)
            raw = _raw(work, path, url)
            yield WorkMentionDocument(
                id=work.title,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                title=stripped_title,
                composer=composer.label,
                raw=raw,
            )
            yield EntityDocument(
                id=work.title,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                name=work.title,
                kind="work",
                raw=raw,
                claims=_claims(composer, work),
            )
        log.info("imslp_works: %d works, %d pages skipped", works, skipped)
