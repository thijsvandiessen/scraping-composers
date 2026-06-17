"""FastAPI application exposing composer data.

Run with:
    uvicorn composer_ingest.api:app

Install the ``api`` extra first:
    pip install -e ".[api]"
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from .db import get_engine, init_db
from .models import Claim, Entity, Source

app = FastAPI(title="Composer API")

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
    id: int
    label: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ClaimOut(BaseModel):
    predicate: str
    value: str | None
    object_label: str | None
    source: str


class ComposerDetail(BaseModel):
    id: int
    label: str
    kind: str
    created_at: datetime
    claims: list[ClaimOut]


class ComposerPage(BaseModel):
    items: list[ComposerSummary]
    total: int
    page: int
    limit: int


@app.get("/composers", response_model=ComposerPage)
def list_composers(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComposerPage:
    base = select(Entity).where(Entity.kind == "person")
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


@app.get("/composers/{composer_id}", response_model=ComposerDetail)
def get_composer(composer_id: int, db: DbSession) -> ComposerDetail:
    entity = db.get(Entity, composer_id)
    if entity is None or entity.kind != "person":
        raise HTTPException(status_code=404, detail="composer not found")

    obj = aliased(Entity)
    rows = db.execute(
        select(Claim.predicate, Claim.value, obj.label, Source.name)
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
            ClaimOut(predicate=pred, value=val, object_label=obj_label, source=src)
            for pred, val, obj_label, src in rows
        ],
    )
