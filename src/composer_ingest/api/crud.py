import uuid

from fastapi import HTTPException
from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session, aliased

from ..models import Claim, Entity, Source
from .schemas import ClaimOut, ComposerDetail, ComposerPage, ComposerSummary


def profession_filter(profession: str) -> ColumnElement[bool]:
    prof_id = (
        select(Entity.id).where(Entity.kind == "profession", Entity.label == profession).scalar_subquery()
    )
    return exists().where(
        Claim.subject_id == Entity.id,
        Claim.predicate == "has_profession",
        Claim.object_id == prof_id,
    )


def list_people(
    db: Session,
    q: str | None,
    page: int,
    limit: int,
    profession: str | None = None,
) -> ComposerPage:
    base = select(Entity).where(Entity.kind == "person")
    if profession is not None:
        base = base.where(profession_filter(profession))
    if q:
        base = base.where(Entity.label.ilike(f"%{q}%"))

    total = db.scalar(select(func.count()).select_from(base.subquery()))
    items = db.scalars(base.order_by(Entity.label).offset((page - 1) * limit).limit(limit)).all()

    return ComposerPage(
        items=[ComposerSummary.model_validate(e) for e in items],
        total=total or 0,
        page=page,
        limit=limit,
    )


def get_person(
    db: Session, person_id: uuid.UUID, profession: str | None, not_found_detail: str
) -> ComposerDetail:
    entity = db.get(Entity, person_id)
    if entity is None or entity.kind != "person":
        raise HTTPException(status_code=404, detail=not_found_detail)

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
            raise HTTPException(status_code=404, detail=not_found_detail)

    obj = aliased(Entity)
    rows = db.execute(
        select(Claim.predicate, Claim.value, obj.label, Source.name, Source.base_url)
        .join(Source, Source.id == Claim.source_id)
        .outerjoin(obj, obj.id == Claim.object_id)
        .where(Claim.subject_id == entity.id)
        .order_by(Claim.predicate, Source.name)
    ).all()

    return ComposerDetail(
        id=entity.id,
        label=entity.label,
        kind=entity.kind,
        created_at=entity.created_at,
        claims=[
            ClaimOut(predicate=pred, value=val, object_label=obj_label, source=src, source_url=src_url)
            for pred, val, obj_label, src, src_url in rows
        ],
    )
