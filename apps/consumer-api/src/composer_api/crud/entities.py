import uuid

from composer_models import (
    Claim,
    Entity,
    EntityRecord,
    PersonMatch,
    RawWorkMention,
    Source,
    Work,
    WorkTitle,
)
from composer_models.normalize import dedup_key
from sqlalchemy import Select, UnaryExpression, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..deps import Pagination
from ..errors import NotFoundError
from ..schemas import EntityDetail, EntityPage, EntitySummary, IncomingClaimOut, StatsOut
from .common import INCOMING_CLAIMS_CAP, outgoing_claims


def get_stats(db: Session) -> StatsOut:
    def count(query: Select[tuple[int]]) -> int:
        return db.scalar(query) or 0

    entities_by_kind = dict(
        db.execute(select(Entity.kind, func.count(Entity.id)).group_by(Entity.kind).order_by(Entity.kind))
        .tuples()
        .all()
    )
    records_by_source = dict(
        db.execute(
            select(Source.name, func.count(EntityRecord.id))
            .join(EntityRecord, EntityRecord.source_id == Source.id)
            .group_by(Source.name)
            .order_by(Source.name)
        )
        .tuples()
        .all()
    )
    mentions_by_status = dict(
        db.execute(
            select(RawWorkMention.match_status, func.count(RawWorkMention.id))
            .group_by(RawWorkMention.match_status)
            .order_by(RawWorkMention.match_status)
        )
        .tuples()
        .all()
    )
    return StatsOut(
        entities_total=count(select(func.count(Entity.id))),
        entities_by_kind=entities_by_kind,
        claims=count(select(func.count(Claim.id))),
        records=count(select(func.count(EntityRecord.id))),
        records_by_source=records_by_source,
        works=count(select(func.count(Work.id))),
        work_titles=count(select(func.count(WorkTitle.id))),
        work_mentions=count(select(func.count(RawWorkMention.id))),
        mentions_by_status=mentions_by_status,
        persons_linked=count(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None))),
        person_matches_to_review=count(
            select(func.count(PersonMatch.id)).where(PersonMatch.status == "needs_review")
        ),
    )


def list_entities(
    db: Session, q: str | None, kind: str | None, pager: Pagination, order: str = "label"
) -> EntityPage:
    base = select(Entity)
    ordering: list[UnaryExpression[bool]] = []
    if kind:
        base = base.where(Entity.kind == kind)
    if q:
        key = dedup_key(q)
        base = base.where(or_(Entity.dedup_key == key, Entity.label.ilike(f"%{q}%")))
        # exact matches before substring matches ("Bonn" before "Abonnema")
        ordering.append((Entity.dedup_key == key).desc())

    total = db.scalar(select(func.count()).select_from(base.subquery()))
    if order == "random":
        # spot-checking mode: a fresh random sample each request
        query = base.order_by(func.random()).limit(pager.limit)
    else:
        query = base.order_by(*ordering, Entity.kind, Entity.label).offset(pager.offset).limit(pager.limit)
    items = db.scalars(query).all()

    return EntityPage(
        items=[EntitySummary.model_validate(e) for e in items],
        total=total or 0,
        page=pager.page,
        limit=pager.limit,
    )


def get_entity(db: Session, entity_id: uuid.UUID) -> EntityDetail:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise NotFoundError("entity not found")

    incoming_total = db.scalar(select(func.count(Claim.id)).where(Claim.object_id == entity.id)) or 0
    subject = aliased(Entity)
    incoming_rows = db.execute(
        select(subject.id, subject.label, Claim.predicate, Source.name)
        .join(Claim, Claim.subject_id == subject.id)
        .join(Source, Source.id == Claim.source_id)
        .where(Claim.object_id == entity.id)
        .order_by(subject.label)
        .limit(INCOMING_CLAIMS_CAP)
    ).all()

    return EntityDetail(
        id=entity.id,
        label=entity.label,
        kind=entity.kind,
        created_at=entity.created_at,
        canonical_entity_id=entity.canonical_entity_id,
        claims=outgoing_claims(db, entity.id),
        incoming_total=incoming_total,
        incoming=[
            IncomingClaimOut(subject_id=sid, subject_label=slabel, predicate=pred, source=src)
            for sid, slabel, pred, src in incoming_rows
        ],
    )
