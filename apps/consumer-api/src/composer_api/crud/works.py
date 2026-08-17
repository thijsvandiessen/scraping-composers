import uuid

from composer_warehouse.models import (
    Concert,
    ConcertWork,
    Entity,
    RawWorkMention,
    Recording,
    RecordingWork,
    Source,
    Work,
    WorkTitle,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from ..deps import Pagination
from ..errors import NotFoundError
from ..schemas import (
    ComposerWorkOut,
    ComposerWorksPage,
    MentionOut,
    MentionPage,
    WorkPage,
    WorkProofOut,
    WorkSummary,
)
from .common import WORK_PROOF_CAP


def list_mentions(db: Session, status: str | None, page: int, limit: int) -> MentionPage:
    """Work mentions with the matcher's decision, best-scored first.

    ``status=needs_review`` is the review queue: mentions the matcher wasn't
    confident about, each with its best candidate work.
    """
    resolved = aliased(Work)
    candidate = aliased(Work)
    base = (
        select(RawWorkMention, Source.name, resolved.canonical_title, candidate.canonical_title)
        .join(Source, Source.id == RawWorkMention.source_id)
        .outerjoin(resolved, resolved.id == RawWorkMention.work_id)
        .outerjoin(candidate, candidate.id == RawWorkMention.candidate_work_id)
    )
    if status:
        base = base.where(RawWorkMention.match_status == status)

    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(
        base.order_by(RawWorkMention.match_score.desc().nulls_last(), RawWorkMention.id)
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()

    items = [
        MentionOut(
            id=mention.id,
            source=source_name,
            composer=mention.raw_composer,
            title=mention.raw_title,
            status=mention.match_status,
            score=mention.match_score,
            method=mention.match_method,
            work_id=mention.work_id,
            work_title=work_title,
            candidate_work_id=mention.candidate_work_id,
            candidate_title=candidate_title,
        )
        for mention, source_name, work_title, candidate_title in rows
    ]
    return MentionPage(items=items, total=total or 0, page=page, limit=limit)


def list_works(
    db: Session, q: str | None, pager: Pagination, performed_only: bool = False, sort: str = "label"
) -> WorkPage:
    composer = aliased(Entity)
    base = select(Work).outerjoin(composer, composer.id == Work.composer_entity_id)
    if q:
        base = base.where(or_(Work.canonical_title.ilike(f"%{q}%"), composer.label.ilike(f"%{q}%")))
    if performed_only:
        base = base.where(
            Work.id.in_(
                select(RawWorkMention.work_id)
                .join(ConcertWork, ConcertWork.mention_id == RawWorkMention.id)
                .where(RawWorkMention.work_id.is_not(None))
            )
        )

    total = db.scalar(select(func.count()).select_from(base.subquery()))

    mention_counts = (
        select(
            RawWorkMention.work_id.label("work_id"),
            func.count(RawWorkMention.id).label("mention_count"),
        )
        .where(RawWorkMention.work_id.is_not(None))
        .group_by(RawWorkMention.work_id)
        .subquery()
    )
    query = base.add_columns(composer.label, mention_counts.c.mention_count).outerjoin(
        mention_counts, mention_counts.c.work_id == Work.id
    )
    if sort == "mentions":
        query = query.order_by(mention_counts.c.mention_count.desc().nulls_last(), Work.canonical_title)
    else:
        query = query.order_by(Work.canonical_title)
    rows = db.execute(query.offset(pager.offset).limit(pager.limit)).all()

    # all alias titles for the page's works in one query, instead of one
    # query (plus a lazy composer load) per row
    titles_by_work: dict[uuid.UUID, list[str]] = {}
    work_ids = [work.id for work, _composer_label, _count in rows]
    if work_ids:
        for work_id, title in db.execute(
            select(WorkTitle.work_id, WorkTitle.title).where(WorkTitle.work_id.in_(work_ids)).distinct()
        ):
            titles_by_work.setdefault(work_id, []).append(title)

    items = []
    for work, composer_label, mention_count in rows:
        aliases = [title for title in titles_by_work.get(work.id, []) if title != work.canonical_title]
        catalogue = f"{work.catalogue_prefix or ''} {work.catalogue_number or ''}".strip() or None
        items.append(
            WorkSummary(
                id=work.id,
                canonical_title=work.canonical_title,
                composer_id=work.composer_entity_id,
                composer_label=composer_label,
                work_type=work.work_type,
                opus_number=work.opus_number,
                catalogue=catalogue,
                musical_key=work.musical_key,
                number=work.number,
                mention_count=mention_count or 0,
                aliases=aliases,
            )
        )

    return WorkPage(items=items, total=total or 0, page=pager.page, limit=pager.limit)


def composer_works(
    db: Session, composer_id: uuid.UUID, pager: Pagination, sort: str = "label"
) -> ComposerWorksPage:
    """A composer's works, each with proof (source, and a concert/recording link when one exists)."""
    entity = db.get(Entity, composer_id)
    if entity is None or entity.kind != "person":
        raise NotFoundError("composer not found")

    base = select(Work).where(Work.composer_entity_id == composer_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    mention_counts = (
        select(
            RawWorkMention.work_id.label("work_id"),
            func.count(RawWorkMention.id).label("mention_count"),
        )
        .where(RawWorkMention.work_id.is_not(None))
        .group_by(RawWorkMention.work_id)
        .subquery()
    )
    query = base.add_columns(mention_counts.c.mention_count).outerjoin(
        mention_counts, mention_counts.c.work_id == Work.id
    )
    if sort == "mentions":
        query = query.order_by(mention_counts.c.mention_count.desc().nulls_last(), Work.canonical_title)
    else:
        query = query.order_by(Work.canonical_title)
    rows = db.execute(query.offset(pager.offset).limit(pager.limit)).all()

    work_ids = [work.id for work, _count in rows]

    # proof per work: every mention that resolved into it, richest first (a
    # concert/recording link with a date beats a bare source attribution), so
    # the WORK_PROOF_CAP keeps the most useful entries when a popular work has
    # hundreds of mentions.
    proofs_by_work: dict[uuid.UUID, list[WorkProofOut]] = {}
    if work_ids:
        seen_by_work: dict[uuid.UUID, set[tuple[str, str | None]]] = {}
        proof_rows = db.execute(
            select(
                RawWorkMention.work_id,
                Source.name,
                Source.base_url,
                Concert.url,
                Concert.date,
                Concert.venue,
                Recording.url,
                Recording.release_date,
            )
            .join(Source, Source.id == RawWorkMention.source_id)
            .outerjoin(ConcertWork, ConcertWork.mention_id == RawWorkMention.id)
            .outerjoin(Concert, Concert.id == ConcertWork.concert_id)
            .outerjoin(RecordingWork, RecordingWork.mention_id == RawWorkMention.id)
            .outerjoin(Recording, Recording.id == RecordingWork.recording_id)
            .where(RawWorkMention.work_id.in_(work_ids))
            .order_by(
                Concert.date.desc().nulls_last(),
                Recording.release_date.desc().nulls_last(),
                RawWorkMention.id,
            )
        ).all()
        for work_id, src_name, src_base, c_url, c_date, c_venue, r_url, r_date in proof_rows:
            bucket = proofs_by_work.setdefault(work_id, [])
            if len(bucket) >= WORK_PROOF_CAP:
                continue
            if c_url or c_date or c_venue:
                proof = WorkProofOut(
                    source=src_name, source_url=c_url or src_base, date=c_date, venue=c_venue
                )
            elif r_url or r_date:
                proof = WorkProofOut(source=src_name, source_url=r_url or src_base, date=r_date, venue=None)
            else:
                proof = WorkProofOut(source=src_name, source_url=src_base, date=None, venue=None)
            seen = seen_by_work.setdefault(work_id, set())
            key = (proof.source, proof.source_url)
            if key in seen:
                continue
            seen.add(key)
            bucket.append(proof)

    items = [
        ComposerWorkOut(
            id=work.id,
            canonical_title=work.canonical_title,
            work_type=work.work_type,
            opus_number=work.opus_number,
            catalogue=f"{work.catalogue_prefix or ''} {work.catalogue_number or ''}".strip() or None,
            musical_key=work.musical_key,
            number=work.number,
            mention_count=mention_count or 0,
            proof=proofs_by_work.get(work.id, []),
        )
        for work, mention_count in rows
    ]

    return ComposerWorksPage(
        composer_id=entity.id,
        composer_label=entity.label,
        items=items,
        total=total,
        page=pager.page,
        limit=pager.limit,
    )
