"""IMSLP work catalogue (imslp.org) — all of it.

This source used to be driven by gold.db: it took gold's curated composer list,
guessed each one's IMSLP category URL and crawled the work lists under it. That
made it the one source here that discovered *who* to scrape from somewhere
other than the source, and it capped the catalogue at whoever gold already knew
— 7,256 works, against the ~267,000 IMSLP lists.

The scoping is gone. IMSLP publishes the whole catalogue in bulk (see
:mod:`.fetch`), so the work list costs ~267 requests and covers every composer
on the site, including everyone gold has never heard of — which is most of
them, and is the point: a composer earns their way into gold on evidence, and
having their works read is part of how that evidence arrives.

Each work yields two documents sharing its IMSLP page title as their id,
because the warehouse stores those two things in different places (see
``composer_warehouse.ingestion.core``), the same split ``boosey`` uses for its
own work catalogue:

* a :class:`~composer_schema.WorkMentionDocument`, which the work matcher
  resolves to a canonical ``works`` row;
* an :class:`~composer_schema.EntityDocument` of kind ``work``, whose
  :class:`~composer_schema.SourceClaim` s make the instrumentation
  (``has_scoring``) queryable alongside every other claim.

Both are produced from the bulk row alone, so **a work is reported whether or
not its detail page was read**. The page is what adds scoring, key, genre and
composition year, and at one request per work a full sweep of 267,000 of them
is tens of hours — so it is a second pass over the catalogue rather than a
precondition for it, bounded by ``max_pages``.

The entity's label is the page's own composer-qualified title ("Piano Sonata
No.32, Op.111 (Beethoven, Ludwig van)") rather than the bare work title, for
the same reason as ``boosey``: entity dedup keys on the normalised label alone,
so a bare title would merge two composers' identically-named works into one
entity and pool their claims.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .fetch import BASE_URL, WorkRow, iter_works
from .works import ParsedWork, parse_work, strip_composer_suffix

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ImslpWorksAdapter"]


def _claims(row: WorkRow, work: ParsedWork | None) -> tuple[SourceClaim, ...]:
    """Typed assertions about the work, from the worklist and its page.

    The first two hold for every work in the catalogue; the rest are only ever
    as complete as the detail pass that read them.
    """
    claims: list[SourceClaim] = [SourceClaim("composed_by", object_kind="person", object_label=row.composer)]
    if row.catalogue_number:
        claims.append(SourceClaim("catalogue_number", value=row.catalogue_number))
    fields = work.fields if work is not None else {}
    if scoring := fields.get("instrumentation"):
        claims.append(SourceClaim("has_scoring", value=scoring))
    if year := fields.get("composition_year"):
        claims.append(SourceClaim("composed_in", value=year))
    if key := fields.get("key"):
        claims.append(SourceClaim("has_key", value=key))
    if genres := fields.get("genre_categories"):
        claims.append(SourceClaim("has_genre", value=genres))
    return tuple(claims)


def _raw(row: WorkRow, work: ParsedWork | None) -> dict[str, object]:
    """Everything the worklist and the page stated, kept verbatim so a later
    pass can structure fields this adapter does not yet turn into claims.

    ``enriched`` records whether the detail page was actually read, so a
    consumer can tell a work with no scoring from one nobody has looked at.
    """
    return {
        "page_id": row.page_id,
        "url": row.url,
        "title": row.title,
        "composer": row.composer,
        "imslp_catalogue_number": row.catalogue_number,
        "enriched": work is not None,
        **(work.fields if work is not None else {}),
    }


class ImslpWorksAdapter(SourceAdapter):
    name = "imslp_works"
    base_url = BASE_URL
    # A wiki catalogue changes slowly, and a full detail sweep is tens of hours
    # — not worth re-running often.
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Every work IMSLP lists, as a work mention and a work entity each.

        ``max_pages`` caps the number of work detail pages fetched, not the
        catalogue: a run that enriches forty works still reports all ~267,000.
        """
        ingested_at = datetime.now(UTC)
        works = 0
        enriched = 0
        skipped = 0
        for row, document in iter_works(max_details=max_pages):
            work = parse_work(document, row.title) if document is not None else None
            if document is not None and work is None:
                skipped += 1
                log.debug("imslp_works: unusable detail page for %s, using the worklist row", row.url)
            works += 1
            enriched += work is not None
            raw = _raw(row, work)
            yield WorkMentionDocument(
                id=row.title,
                url=row.url,
                source_name=self.name,
                ingested_at=ingested_at,
                title=strip_composer_suffix(row.title, row.composer),
                composer=row.composer,
                raw=raw,
            )
            yield EntityDocument(
                id=row.title,
                url=row.url,
                source_name=self.name,
                ingested_at=ingested_at,
                name=row.title,
                kind="work",
                raw=raw,
                claims=_claims(row, work),
            )
        log.info(
            "imslp_works: %d works, %d enriched from their page, %d pages unusable",
            works,
            enriched,
            skipped,
        )
