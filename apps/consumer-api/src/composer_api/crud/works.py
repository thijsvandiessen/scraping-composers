import uuid

from composer_warehouse.models import ConcertWork, Entity, RawWorkMention, Source, Work, WorkTitle
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from ..deps import Pagination
from ..schemas import MentionOut, MentionPage, WorkPage, WorkSummary


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
