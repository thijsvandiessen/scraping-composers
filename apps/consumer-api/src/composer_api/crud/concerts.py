import uuid

from composer_models import (
    Concert,
    ConcertParticipant,
    ConcertWork,
    Entity,
    RawWorkMention,
    Source,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..errors import NotFoundError
from ..schemas import (
    ConcertDetail,
    ConcertListPage,
    ConcertOut,
    ConcertPage,
    ConcertParticipantOut,
    ConcertSummary,
    ConcertWorkOut,
)
from .common import CONCERT_WORKS_CAP


def person_concerts(db: Session, person_id: uuid.UUID, page: int, limit: int) -> ConcertPage:
    """Concerts a person took part in, newest first (populated in gold)."""
    entity = db.get(Entity, person_id)
    if entity is None or entity.kind != "person":
        raise NotFoundError("person not found")

    base = (
        select(Concert, ConcertParticipant.role, Source.name)
        .join(ConcertParticipant, ConcertParticipant.concert_id == Concert.id)
        .join(Source, Source.id == Concert.source_id)
        .where(ConcertParticipant.entity_id == person_id)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(Concert.date.desc().nulls_last(), Concert.id).offset((page - 1) * limit).limit(limit)
    ).all()

    # works performed at the page's concerts, capped per concert
    works_by_concert: dict[int, list[str]] = {}
    concert_ids = [concert.id for concert, _role, _src in rows]
    if concert_ids:
        work_rows = db.execute(
            select(ConcertWork.concert_id, RawWorkMention.raw_title)
            .join(RawWorkMention, RawWorkMention.id == ConcertWork.mention_id)
            .where(ConcertWork.concert_id.in_(concert_ids))
            .order_by(ConcertWork.id)
        ).all()
        for concert_id, title in work_rows:
            titles = works_by_concert.setdefault(concert_id, [])
            if len(titles) < CONCERT_WORKS_CAP:
                titles.append(title)

    return ConcertPage(
        person_id=entity.id,
        person_label=entity.label,
        items=[
            ConcertOut(
                id=concert.id,
                source=source_name,
                date=concert.date,
                venue=concert.venue,
                season=concert.season,
                url=concert.url,
                role=role,
                works=works_by_concert.get(concert.id, []),
            )
            for concert, role, source_name in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )


def list_concerts(db: Session, q: str | None, source: str | None, page: int, limit: int) -> ConcertListPage:
    """Browse concerts, newest first. ``q`` matches the venue or a participant name."""
    base = select(Concert, Source.name).join(Source, Source.id == Concert.source_id)
    if source:
        base = base.where(Source.name == source)
    if q:
        base = base.where(
            or_(
                Concert.venue.ilike(f"%{q}%"),
                Concert.id.in_(
                    select(ConcertParticipant.concert_id).where(ConcertParticipant.name.ilike(f"%{q}%"))
                ),
            )
        )

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(Concert.date.desc().nulls_last(), Concert.id).offset((page - 1) * limit).limit(limit)
    ).all()

    concert_ids = [concert.id for concert, _src in rows]
    conductors_by_concert: dict[int, list[str]] = {}
    soloists_by_concert: dict[int, int] = {}
    works_by_concert: dict[int, int] = {}
    if concert_ids:
        for concert_id, role, name in db.execute(
            select(ConcertParticipant.concert_id, ConcertParticipant.role, ConcertParticipant.name)
            .where(ConcertParticipant.concert_id.in_(concert_ids))
            .order_by(ConcertParticipant.name)
        ):
            if role == "conductor":
                conductors_by_concert.setdefault(concert_id, []).append(name)
            else:
                soloists_by_concert[concert_id] = soloists_by_concert.get(concert_id, 0) + 1
        works_by_concert = dict(
            db.execute(
                select(ConcertWork.concert_id, func.count(ConcertWork.id))
                .where(ConcertWork.concert_id.in_(concert_ids))
                .group_by(ConcertWork.concert_id)
            )
            .tuples()
            .all()
        )

    return ConcertListPage(
        items=[
            ConcertSummary(
                id=concert.id,
                source=source_name,
                date=concert.date,
                venue=concert.venue,
                season=concert.season,
                event_type=concert.event_type,
                url=concert.url,
                conductors=conductors_by_concert.get(concert.id, []),
                soloist_count=soloists_by_concert.get(concert.id, 0),
                work_count=works_by_concert.get(concert.id, 0),
            )
            for concert, source_name in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )


def get_concert(db: Session, concert_id: int) -> ConcertDetail:
    row = db.execute(
        select(Concert, Source.name)
        .join(Source, Source.id == Concert.source_id)
        .where(Concert.id == concert_id)
    ).first()
    if row is None:
        raise NotFoundError("concert not found")
    concert, source_name = row

    participants = db.execute(
        select(ConcertParticipant)
        .where(ConcertParticipant.concert_id == concert.id)
        .order_by(ConcertParticipant.role, ConcertParticipant.name)
    ).scalars()
    works = db.execute(
        select(RawWorkMention.raw_title, RawWorkMention.raw_composer)
        .join(ConcertWork, ConcertWork.mention_id == RawWorkMention.id)
        .where(ConcertWork.concert_id == concert.id)
        .order_by(ConcertWork.id)
    ).all()

    return ConcertDetail(
        id=concert.id,
        source=source_name,
        date=concert.date,
        venue=concert.venue,
        season=concert.season,
        event_type=concert.event_type,
        url=concert.url,
        participants=[
            ConcertParticipantOut(role=p.role, name=p.name, discipline=p.discipline, entity_id=p.entity_id)
            for p in participants
        ],
        works=[ConcertWorkOut(title=title, composer=composer) for title, composer in works],
    )
