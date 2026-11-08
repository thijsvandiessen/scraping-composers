"""How often each composer's works are programmed, kept as a table of its own.

``/v1/composers`` selects, counts and sorts composers by the concerts and
recordings their works are on. Aggregating that from ``concert_works`` and
``recording_works`` takes most of a second on a full corpus, and the API would
pay it twice per page (total and rows), so the counts are stored here instead.
``derive_concerts`` and ``derive_recordings`` refresh them in silver after
rebuilding their tables; promote refreshes them in gold after copying, because
gold re-points mentions at each duplicate's canonical person.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Connection,
    ForeignKey,
    Index,
    Integer,
    Uuid,
    cast,
    delete,
    func,
    insert,
    literal,
    select,
    union_all,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base
from .concerts import ConcertWork
from .recordings import RecordingWork
from .works import RawWorkMention


class ComposerProgramme(Base):
    """Concerts and recordings a person's works are on (not the person's own credits)."""

    __tablename__ = "composer_programmes"
    __table_args__ = (
        Index("ix_composer_programmes_concerts", "concert_count"),
        Index("ix_composer_programmes_recordings", "recording_count"),
        Index("ix_composer_programmes_total", "programme_count"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("entities.id"), primary_key=True)
    concert_count: Mapped[int] = mapped_column(Integer)
    recording_count: Mapped[int] = mapped_column(Integer)
    programme_count: Mapped[int] = mapped_column(Integer)  # concerts + recordings


def refresh_composer_programmes(db: Session | Connection) -> None:
    """Recompute every composer's counts from the event tables.

    Concert and recording ids are counted distinct per table, in one grouped
    pass each, then summed per composer. Does not commit.
    """
    per_table = [
        select(
            RawWorkMention.composer_entity_id.label("entity_id"),
            func.count(func.distinct(ConcertWork.concert_id)).label("concert_count"),
            literal(0).label("recording_count"),
        )
        .join(RawWorkMention, RawWorkMention.id == ConcertWork.mention_id)
        .where(RawWorkMention.composer_entity_id.is_not(None))
        .group_by(RawWorkMention.composer_entity_id),
        select(
            RawWorkMention.composer_entity_id.label("entity_id"),
            literal(0).label("concert_count"),
            func.count(func.distinct(RecordingWork.recording_id)).label("recording_count"),
        )
        .join(RawWorkMention, RawWorkMention.id == RecordingWork.mention_id)
        .where(RawWorkMention.composer_entity_id.is_not(None))
        .group_by(RawWorkMention.composer_entity_id),
    ]
    both = union_all(*per_table).subquery()
    # cast: Postgres sums integers into numeric
    concerts = cast(func.sum(both.c.concert_count), Integer)
    recordings = cast(func.sum(both.c.recording_count), Integer)
    counts = select(both.c.entity_id, concerts, recordings, concerts + recordings).group_by(both.c.entity_id)

    db.execute(delete(ComposerProgramme))
    db.execute(
        insert(ComposerProgramme).from_select(
            ["entity_id", "concert_count", "recording_count", "programme_count"], counts
        )
    )
