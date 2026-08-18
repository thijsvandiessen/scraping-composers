import uuid

from composer_models import (
    Entity,
    RawWorkMention,
    Recording,
    RecordingParticipant,
    RecordingWork,
    Source,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..errors import NotFoundError
from ..schemas import (
    RecordingDetail,
    RecordingListPage,
    RecordingOut,
    RecordingPage,
    RecordingParticipantOut,
    RecordingSummary,
    RecordingWorkOut,
)
from .common import CONCERT_WORKS_CAP


def person_recordings(db: Session, person_id: uuid.UUID, page: int, limit: int) -> RecordingPage:
    """Recordings a person is credited on, newest first (populated in gold)."""
    entity = db.get(Entity, person_id)
    if entity is None or entity.kind != "person":
        raise NotFoundError("person not found")

    base = (
        select(Recording, RecordingParticipant.role, Source.name)
        .join(RecordingParticipant, RecordingParticipant.recording_id == Recording.id)
        .join(Source, Source.id == Recording.source_id)
        .where(RecordingParticipant.entity_id == person_id)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(Recording.release_date.desc().nulls_last(), Recording.id)
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()

    # works on the page's recordings, capped per recording
    works_by_recording: dict[int, list[str]] = {}
    recording_ids = [recording.id for recording, _role, _src in rows]
    if recording_ids:
        work_rows = db.execute(
            select(RecordingWork.recording_id, RawWorkMention.raw_title)
            .join(RawWorkMention, RawWorkMention.id == RecordingWork.mention_id)
            .where(RecordingWork.recording_id.in_(recording_ids))
            .order_by(RecordingWork.id)
        ).all()
        for recording_id, title in work_rows:
            titles = works_by_recording.setdefault(recording_id, [])
            if len(titles) < CONCERT_WORKS_CAP:
                titles.append(title)

    return RecordingPage(
        person_id=entity.id,
        person_label=entity.label,
        items=[
            RecordingOut(
                id=recording.id,
                source=source_name,
                title=recording.title,
                release_date=recording.release_date,
                label=recording.label,
                catalogue_number=recording.catalogue_number,
                format=recording.format,
                url=recording.url,
                role=role,
                works=works_by_recording.get(recording.id, []),
            )
            for recording, role, source_name in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )


def list_recordings(
    db: Session, q: str | None, source: str | None, page: int, limit: int
) -> RecordingListPage:
    """Browse recordings, newest first. ``q`` matches the title or a participant name."""
    base = select(Recording, Source.name).join(Source, Source.id == Recording.source_id)
    if source:
        base = base.where(Source.name == source)
    if q:
        base = base.where(
            or_(
                Recording.title.ilike(f"%{q}%"),
                Recording.id.in_(
                    select(RecordingParticipant.recording_id).where(RecordingParticipant.name.ilike(f"%{q}%"))
                ),
            )
        )

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(Recording.release_date.desc().nulls_last(), Recording.id)
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()

    recording_ids = [recording.id for recording, _src in rows]
    conductors_by_recording: dict[int, list[str]] = {}
    performers_by_recording: dict[int, int] = {}
    works_by_recording: dict[int, int] = {}
    if recording_ids:
        for recording_id, role, name in db.execute(
            select(RecordingParticipant.recording_id, RecordingParticipant.role, RecordingParticipant.name)
            .where(RecordingParticipant.recording_id.in_(recording_ids))
            .order_by(RecordingParticipant.name)
        ):
            if role == "conductor":
                conductors_by_recording.setdefault(recording_id, []).append(name)
            else:
                performers_by_recording[recording_id] = performers_by_recording.get(recording_id, 0) + 1
        works_by_recording = dict(
            db.execute(
                select(RecordingWork.recording_id, func.count(RecordingWork.id))
                .where(RecordingWork.recording_id.in_(recording_ids))
                .group_by(RecordingWork.recording_id)
            )
            .tuples()
            .all()
        )

    return RecordingListPage(
        items=[
            RecordingSummary(
                id=recording.id,
                source=source_name,
                title=recording.title,
                release_date=recording.release_date,
                label=recording.label,
                catalogue_number=recording.catalogue_number,
                format=recording.format,
                url=recording.url,
                conductors=conductors_by_recording.get(recording.id, []),
                performer_count=performers_by_recording.get(recording.id, 0),
                work_count=works_by_recording.get(recording.id, 0),
            )
            for recording, source_name in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )


def get_recording(db: Session, recording_id: int) -> RecordingDetail:
    row = db.execute(
        select(Recording, Source.name)
        .join(Source, Source.id == Recording.source_id)
        .where(Recording.id == recording_id)
    ).first()
    if row is None:
        raise NotFoundError("recording not found")
    recording, source_name = row

    participants = db.execute(
        select(RecordingParticipant)
        .where(RecordingParticipant.recording_id == recording.id)
        .order_by(RecordingParticipant.role, RecordingParticipant.name)
    ).scalars()
    works = db.execute(
        select(RawWorkMention.raw_title, RawWorkMention.raw_composer)
        .join(RecordingWork, RecordingWork.mention_id == RawWorkMention.id)
        .where(RecordingWork.recording_id == recording.id)
        .order_by(RecordingWork.id)
    ).all()

    return RecordingDetail(
        id=recording.id,
        source=source_name,
        title=recording.title,
        release_date=recording.release_date,
        label=recording.label,
        catalogue_number=recording.catalogue_number,
        format=recording.format,
        url=recording.url,
        participants=[
            RecordingParticipantOut(role=p.role, name=p.name, discipline=p.discipline, entity_id=p.entity_id)
            for p in participants
        ],
        works=[RecordingWorkOut(title=title, composer=composer) for title, composer in works],
    )
