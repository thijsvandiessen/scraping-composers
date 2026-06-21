import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from .crud import get_person, list_people
from .deps import DbSession
from .schemas import ComposerDetail, ComposerPage

v1 = APIRouter(prefix="/v1")


@v1.get("/composers", response_model=ComposerPage)
def list_composers(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComposerPage:
    return list_people(db, q, page, limit)


@v1.get("/composers/{composer_id}", response_model=ComposerDetail)
def get_composer(composer_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return get_person(db, composer_id, None, "composer not found")


@v1.get("/soloists", response_model=ComposerPage)
def list_soloists(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComposerPage:
    return list_people(db, q, page, limit, profession="soloist")


@v1.get("/soloists/{soloist_id}", response_model=ComposerDetail)
def get_soloist(soloist_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return get_person(db, soloist_id, "soloist", "soloist not found")


@v1.get("/conductors", response_model=ComposerPage)
def list_conductors(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComposerPage:
    return list_people(db, q, page, limit, profession="conductor")


@v1.get("/conductors/{conductor_id}", response_model=ComposerDetail)
def get_conductor(conductor_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return get_person(db, conductor_id, "conductor", "conductor not found")
