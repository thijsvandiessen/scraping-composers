import uuid

from composer_models import (
    Claim,
    ConcertParticipant,
    Entity,
    EntityRecord,
    RecordingParticipant,
    Source,
    Work,
)
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from ..deps import Pagination, PeopleFilters
from ..errors import NotFoundError
from ..schemas import ComposerDetail, ComposerPage, ComposerSummary
from .common import outgoing_claims
from .genres import genre_filter

# Works a person needs to count as a composer. Not the ``composer`` profession
# claim: wikidata's occupation tags pop stars, poets and conductors as
# composers, while thousands of real composers in the work sources carry no
# profession at all. One or two works is mostly a conductor's or arranger's
# stray piece.
COMPOSER_MIN_WORKS = 3


def _profession_id(db: Session, profession: str) -> uuid.UUID | None:
    return db.scalar(select(Entity.id).where(Entity.kind == "profession", Entity.label == profession))


def profession_filter(prof_id: uuid.UUID) -> ColumnElement[bool]:
    # Deliberately an uncorrelated IN, with the profession id resolved up
    # front: SQLite materializes the subquery once and probes it per person.
    # A correlated EXISTS here scanned all claims of the profession for every
    # person row (billions of comparisons — minutes instead of milliseconds).
    return Entity.id.in_(
        select(Claim.subject_id).where(Claim.predicate == "has_profession", Claim.object_id == prof_id)
    )


def works_filter(min_works: int) -> ColumnElement[bool]:
    # Uncorrelated IN for the same reason as ``profession_filter``: one grouped
    # pass over works (indexed on composer_entity_id), probed per person.
    return Entity.id.in_(
        select(Work.composer_entity_id)
        .where(Work.composer_entity_id.is_not(None))
        .group_by(Work.composer_entity_id)
        .having(func.count() >= min_works)
    )


def source_filter(source: str) -> ColumnElement[bool]:
    # Uncorrelated IN for the same reason as ``profession_filter``, and so the
    # outer query keeps one row per person: joining entity_records in directly
    # fans out by the number of records the source reported.
    return Entity.id.in_(
        select(EntityRecord.entity_id)
        .join(Source, Source.id == EntityRecord.source_id)
        .where(Source.name == source, EntityRecord.entity_id.is_not(None))
    )


def list_people(
    db: Session,
    filters: PeopleFilters,
    pager: Pagination,
    role: str,
    sort: str = "label",
) -> ComposerPage:
    """People in ``role``: "composer" by work count, any other role by its
    ``has_profession`` claim."""
    base = select(Entity).where(Entity.kind == "person")
    if filters.source:
        base = base.where(source_filter(filters.source))
    if role == "composer":
        base = base.where(works_filter(COMPOSER_MIN_WORKS))
    else:
        prof_id = _profession_id(db, role)
        if prof_id is None:
            return ComposerPage(items=[], total=0, page=pager.page, limit=pager.limit)
        base = base.where(profession_filter(prof_id))
    if filters.q:
        base = base.where(Entity.label.ilike(f"%{filters.q}%"))
    if filters.genre is not None:
        base = base.where(genre_filter(filters.genre))

    total = db.scalar(select(func.count()).select_from(base.subquery()))

    # concerts per person as an uncorrelated aggregate, outer-joined in
    concert_counts = (
        select(
            ConcertParticipant.entity_id.label("entity_id"),
            func.count(func.distinct(ConcertParticipant.concert_id)).label("concert_count"),
        )
        .group_by(ConcertParticipant.entity_id)
        .subquery()
    )

    # recordings per person as an uncorrelated aggregate, outer-joined in
    recording_counts = (
        select(
            RecordingParticipant.entity_id.label("entity_id"),
            func.count(func.distinct(RecordingParticipant.recording_id)).label("recording_count"),
        )
        .group_by(RecordingParticipant.entity_id)
        .subquery()
    )

    query = (
        base.add_columns(concert_counts.c.concert_count, recording_counts.c.recording_count)
        .outerjoin(concert_counts, concert_counts.c.entity_id == Entity.id)
        .outerjoin(recording_counts, recording_counts.c.entity_id == Entity.id)
    )
    if sort == "concerts":
        query = query.order_by(concert_counts.c.concert_count.desc().nulls_last(), Entity.label)
    elif sort == "recordings":
        query = query.order_by(recording_counts.c.recording_count.desc().nulls_last(), Entity.label)
    else:
        query = query.order_by(Entity.label)
    rows = db.execute(query.offset(pager.offset).limit(pager.limit)).all()

    return ComposerPage(
        items=[
            ComposerSummary(
                id=entity.id,
                label=entity.label,
                created_at=entity.created_at,
                concert_count=concert_count or 0,
                recording_count=recording_count or 0,
            )
            for entity, concert_count, recording_count in rows
        ],
        total=total or 0,
        page=pager.page,
        limit=pager.limit,
    )


def get_person(
    db: Session, person_id: uuid.UUID, profession: str | None, not_found_detail: str
) -> ComposerDetail:
    entity = db.get(Entity, person_id)
    if entity is None or entity.kind != "person":
        raise NotFoundError(not_found_detail)

    if profession is not None:
        prof_id = db.scalar(select(Entity.id).where(Entity.kind == "profession", Entity.label == profession))
        has_prof = prof_id is not None and db.scalar(
            select(func.count()).where(
                Claim.subject_id == entity.id,
                Claim.predicate == "has_profession",
                Claim.object_id == prof_id,
            )
        )
        if not has_prof:
            raise NotFoundError(not_found_detail)

    return ComposerDetail(
        id=entity.id,
        label=entity.label,
        kind=entity.kind,
        created_at=entity.created_at,
        claims=outgoing_claims(db, entity.id),
    )
