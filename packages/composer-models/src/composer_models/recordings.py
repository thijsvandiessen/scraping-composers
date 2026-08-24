"""Recording model, derived into silver by the ``derive-recordings`` pass.

The album/release counterpart to ``concerts``: silver keeps each release's
context inside ``raw_work_mentions.raw`` (marked ``_source: "llm"``,
``_kind: "recording"``); ``derive_recordings`` groups those mentions into
recordings per source and links the people involved. The promote step copies
the tables into gold, collapsing participant links to their canonical entities.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .core import Source
from .works import RawWorkMention


class Recording(Base):
    """One recording/album release as derived from work mentions."""

    __tablename__ = "recordings"
    __table_args__ = (UniqueConstraint("source_id", "external_key", name="uq_recording_source_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_key: Mapped[str] = mapped_column(Text)  # per-source recording identity
    title: Mapped[str | None] = mapped_column(Text)
    release_date: Mapped[str | None] = mapped_column(Text)  # ISO where derivable
    label: Mapped[str | None] = mapped_column(Text)
    catalogue_number: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(Text)  # CD | Vinyl | Digital | ...
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship()
    participants: Mapped[list[RecordingParticipant]] = relationship(back_populates="recording")
    works: Mapped[list[RecordingWork]] = relationship(back_populates="recording")


class RecordingParticipant(Base):
    """A participant's role on a recording (conductor | soloist | ensemble).

    ``entity_id`` links to the person entity — or, for an ensemble credit, the
    ensemble entity — when the reported name resolves (by dedup key); the
    verbatim ``name`` is always kept.
    """

    __tablename__ = "recording_participants"
    __table_args__ = (
        Index("ix_recording_participants_entity", "entity_id"),
        Index("ix_recording_participants_recording", "recording_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"))
    role: Mapped[str] = mapped_column(String(50))  # conductor | soloist | ensemble
    name: Mapped[str] = mapped_column(Text)
    discipline: Mapped[str | None] = mapped_column(Text)  # soloist instrument/voice
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("entities.id"))

    recording: Mapped[Recording] = relationship(back_populates="participants")


class RecordingWork(Base):
    """A work (mention) that appears on a recording."""

    __tablename__ = "recording_works"
    __table_args__ = (Index("ix_recording_works_recording", "recording_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"))
    mention_id: Mapped[int] = mapped_column(ForeignKey("raw_work_mentions.id"))

    recording: Mapped[Recording] = relationship(back_populates="works")
    mention: Mapped[RawWorkMention] = relationship()
