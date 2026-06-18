"""FastAPI application exposing composer data.

Run with:
    uvicorn composer_ingest.api:app

Install the ``api`` extra first:
    pip install -e ".[api]"
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from .db import get_engine, init_db
from .models import Claim, Entity, Source

app = FastAPI(title="Composer API")
v1 = APIRouter(prefix="/v1")

_session_factory: sessionmaker[Session] | None = None


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = init_db(get_engine())
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    with _get_session_factory()() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]


class ComposerSummary(BaseModel):
    id: uuid.UUID
    label: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ClaimOut(BaseModel):
    predicate: str
    value: str | None
    object_label: str | None
    source: str
    source_url: str | None


class ComposerDetail(BaseModel):
    id: uuid.UUID
    label: str
    kind: str
    created_at: datetime
    claims: list[ClaimOut]


class ComposerPage(BaseModel):
    items: list[ComposerSummary]
    total: int
    page: int
    limit: int


def _profession_filter(profession: str) -> ColumnElement[bool]:
    prof_id = (
        select(Entity.id).where(Entity.kind == "profession", Entity.label == profession).scalar_subquery()
    )
    return exists().where(
        Claim.subject_id == Entity.id,
        Claim.predicate == "has_profession",
        Claim.object_id == prof_id,
    )


def _list_people(
    db: Session,
    q: str | None,
    page: int,
    limit: int,
    profession: str | None = None,
) -> ComposerPage:
    base = select(Entity).where(Entity.kind == "person")
    if profession is not None:
        base = base.where(_profession_filter(profession))
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


def _get_person(
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


@v1.get("/composers", response_model=ComposerPage)
def list_composers(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComposerPage:
    return _list_people(db, q, page, limit)


@v1.get("/composers/{composer_id}", response_model=ComposerDetail)
def get_composer(composer_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return _get_person(db, composer_id, None, "composer not found")


@v1.get("/soloists", response_model=ComposerPage)
def list_soloists(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComposerPage:
    return _list_people(db, q, page, limit, profession="soloist")


@v1.get("/soloists/{soloist_id}", response_model=ComposerDetail)
def get_soloist(soloist_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return _get_person(db, soloist_id, "soloist", "soloist not found")


@v1.get("/conductors", response_model=ComposerPage)
def list_conductors(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComposerPage:
    return _list_people(db, q, page, limit, profession="conductor")


@v1.get("/conductors/{conductor_id}", response_model=ComposerDetail)
def get_conductor(conductor_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return _get_person(db, conductor_id, "conductor", "conductor not found")


app.include_router(v1)
