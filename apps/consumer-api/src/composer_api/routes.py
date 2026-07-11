import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from .crud import (
    get_concert,
    get_entity,
    get_person,
    get_stats,
    list_concerts,
    list_entities,
    list_mentions,
    list_people,
    list_works,
    person_concerts,
)
from .deps import DbSession
from .schemas import (
    ComposerDetail,
    ComposerPage,
    ConcertDetail,
    ConcertListPage,
    ConcertPage,
    EntityDetail,
    EntityPage,
    MentionPage,
    StatsOut,
    WorkPage,
)

v1 = APIRouter(prefix="/v1")


@v1.get("/stats", response_model=StatsOut)
def stats(db: DbSession) -> StatsOut:
    return get_stats(db)


@v1.get("/entities", response_model=EntityPage)
def entities(
    db: DbSession,
    q: str | None = None,
    kind: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Annotated[str, Query(pattern="^(label|random)$")] = "label",
) -> EntityPage:
    return list_entities(db, q, kind, page, limit, order)


@v1.get("/entities/{entity_id}", response_model=EntityDetail)
def entity_detail(entity_id: uuid.UUID, db: DbSession) -> EntityDetail:
    return get_entity(db, entity_id)


@v1.get("/works", response_model=WorkPage)
def works(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    performed: bool = False,
    sort: Annotated[str, Query(pattern="^(label|mentions)$")] = "label",
) -> WorkPage:
    return list_works(db, q, page, limit, performed_only=performed, sort=sort)


@v1.get("/mentions", response_model=MentionPage)
def mentions(
    db: DbSession,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MentionPage:
    return list_mentions(db, status, page, limit)


@v1.get("/concerts", response_model=ConcertListPage)
def concerts(
    db: DbSession,
    q: str | None = None,
    source: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConcertListPage:
    """Browse concerts (derived into gold by promote), newest first."""
    return list_concerts(db, q, source, page, limit)


@v1.get("/concerts/{concert_id}", response_model=ConcertDetail)
def concert_detail(concert_id: int, db: DbSession) -> ConcertDetail:
    """One concert: participants (with roles and disciplines) and its programme."""
    return get_concert(db, concert_id)


@v1.get("/people/{person_id}/concerts", response_model=ConcertPage)
def get_person_concerts(
    person_id: uuid.UUID,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConcertPage:
    """Concerts the person took part in (derived into gold by promote)."""
    return person_concerts(db, person_id, page, limit)


@v1.get("/composers", response_model=ComposerPage)
def list_composers(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str, Query(pattern="^(label|concerts|sitelinks)$")] = "label",
) -> ComposerPage:
    return list_people(db, q, page, limit, profession="composer", sort=sort)


@v1.get("/composers/{composer_id}", response_model=ComposerDetail)
def get_composer(composer_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return get_person(db, composer_id, None, "composer not found")


@v1.get("/soloists", response_model=ComposerPage)
def list_soloists(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str, Query(pattern="^(label|concerts|sitelinks)$")] = "label",
) -> ComposerPage:
    return list_people(db, q, page, limit, profession="soloist", sort=sort)


@v1.get("/soloists/{soloist_id}", response_model=ComposerDetail)
def get_soloist(soloist_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return get_person(db, soloist_id, "soloist", "soloist not found")


@v1.get("/conductors", response_model=ComposerPage)
def list_conductors(
    db: DbSession,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str, Query(pattern="^(label|concerts|sitelinks)$")] = "label",
) -> ComposerPage:
    return list_people(db, q, page, limit, profession="conductor", sort=sort)


@v1.get("/conductors/{conductor_id}", response_model=ComposerDetail)
def get_conductor(conductor_id: uuid.UUID, db: DbSession) -> ComposerDetail:
    return get_person(db, conductor_id, "conductor", "conductor not found")
