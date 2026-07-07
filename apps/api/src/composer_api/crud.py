import uuid

from composer_ingest.etl.models import (
    Claim,
    Concert,
    ConcertParticipant,
    ConcertWork,
    Entity,
    EntityRecord,
    PersonMatch,
    RawWorkMention,
    Source,
    Work,
    WorkTitle,
)
from composer_ingest.etl.normalize import dedup_key
from fastapi import HTTPException
from sqlalchemy import ColumnElement, Select, UnaryExpression, func, or_, select
from sqlalchemy.orm import Session, aliased

from .schemas import (
    ClaimOut,
    ComposerDetail,
    ComposerPage,
    ComposerSummary,
    ConcertDetail,
    ConcertListPage,
    ConcertOut,
    ConcertPage,
    ConcertParticipantOut,
    ConcertSummary,
    ConcertWorkOut,
    EntityDetail,
    EntityPage,
    EntitySummary,
    IncomingClaimOut,
    MentionOut,
    MentionPage,
    StatsOut,
    WorkPage,
    WorkSummary,
)

CONCERT_WORKS_CAP = 20

INCOMING_CLAIMS_CAP = 50


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


def list_people(
    db: Session,
    q: str | None,
    page: int,
    limit: int,
    profession: str | None = None,
    sort: str = "label",
) -> ComposerPage:
    base = select(Entity).where(Entity.kind == "person")
    if profession is not None:
        prof_id = _profession_id(db, profession)
        if prof_id is None:
            return ComposerPage(items=[], total=0, page=page, limit=limit)
        base = base.where(profession_filter(prof_id))
    if q:
        base = base.where(Entity.label.ilike(f"%{q}%"))

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
    query = base.add_columns(concert_counts.c.concert_count).outerjoin(
        concert_counts, concert_counts.c.entity_id == Entity.id
    )
    if sort == "concerts":
        query = query.order_by(concert_counts.c.concert_count.desc().nulls_last(), Entity.label)
    else:
        query = query.order_by(Entity.label)
    rows = db.execute(query.offset((page - 1) * limit).limit(limit)).all()

    return ComposerPage(
        items=[
            ComposerSummary(
                id=entity.id, label=entity.label, created_at=entity.created_at, concert_count=count or 0
            )
            for entity, count in rows
        ],
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

    return ComposerDetail(
        id=entity.id,
        label=entity.label,
        kind=entity.kind,
        created_at=entity.created_at,
        claims=_outgoing_claims(db, entity.id),
    )


def _outgoing_claims(db: Session, entity_id: uuid.UUID) -> list[ClaimOut]:
    obj = aliased(Entity)
    rows = db.execute(
        select(Claim.predicate, Claim.value, obj.label, Claim.object_id, Source.name, Source.base_url)
        .join(Source, Source.id == Claim.source_id)
        .outerjoin(obj, obj.id == Claim.object_id)
        .where(Claim.subject_id == entity_id)
        .order_by(Claim.predicate, Source.name)
    ).all()
    return [
        ClaimOut(
            predicate=pred,
            value=val,
            object_label=obj_label,
            object_id=obj_id,
            source=src,
            source_url=src_url,
        )
        for pred, val, obj_label, obj_id, src, src_url in rows
    ]


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
    db: Session, q: str | None, kind: str | None, page: int, limit: int, order: str = "label"
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
        query = base.order_by(func.random()).limit(limit)
    else:
        query = base.order_by(*ordering, Entity.kind, Entity.label).offset((page - 1) * limit).limit(limit)
    items = db.scalars(query).all()

    return EntityPage(
        items=[EntitySummary.model_validate(e) for e in items],
        total=total or 0,
        page=page,
        limit=limit,
    )


def get_entity(db: Session, entity_id: uuid.UUID) -> EntityDetail:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

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
        claims=_outgoing_claims(db, entity.id),
        incoming_total=incoming_total,
        incoming=[
            IncomingClaimOut(subject_id=sid, subject_label=slabel, predicate=pred, source=src)
            for sid, slabel, pred, src in incoming_rows
        ],
    )


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


def person_concerts(db: Session, person_id: uuid.UUID, page: int, limit: int) -> ConcertPage:
    """Concerts a person took part in, newest first (populated in gold)."""
    entity = db.get(Entity, person_id)
    if entity is None or entity.kind != "person":
        raise HTTPException(status_code=404, detail="person not found")

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
        raise HTTPException(status_code=404, detail="concert not found")
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


def list_works(
    db: Session, q: str | None, page: int, limit: int, performed_only: bool = False, sort: str = "label"
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
    query = base.add_columns(mention_counts.c.mention_count).outerjoin(
        mention_counts, mention_counts.c.work_id == Work.id
    )
    if sort == "mentions":
        query = query.order_by(mention_counts.c.mention_count.desc().nulls_last(), Work.canonical_title)
    else:
        query = query.order_by(Work.canonical_title)
    rows = db.execute(query.offset((page - 1) * limit).limit(limit)).all()

    items = []
    for work, mention_count in rows:
        aliases = [
            title
            for title in db.scalars(
                select(WorkTitle.title).where(WorkTitle.work_id == work.id).distinct()
            ).all()
            if title != work.canonical_title
        ]
        catalogue = f"{work.catalogue_prefix or ''} {work.catalogue_number or ''}".strip() or None
        items.append(
            WorkSummary(
                id=work.id,
                canonical_title=work.canonical_title,
                composer_id=work.composer_entity_id,
                composer_label=work.composer.label if work.composer else None,
                work_type=work.work_type,
                opus_number=work.opus_number,
                catalogue=catalogue,
                musical_key=work.musical_key,
                number=work.number,
                mention_count=mention_count or 0,
                aliases=aliases,
            )
        )

    return WorkPage(items=items, total=total or 0, page=page, limit=limit)
