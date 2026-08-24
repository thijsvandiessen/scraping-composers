"""Concert model, derived into silver by the ``derive-concerts`` pass.

Silver keeps the raw performance context inside ``raw_work_mentions.raw``;
``derive_concerts`` groups those mentions into concerts per source and links
the people involved. The promote step copies the tables into gold, collapsing
participant links to their canonical entities.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .core import Source
from .works import RawWorkMention


class Concert(Base):
    """One concert (or recording session) as derived from work mentions."""

    __tablename__ = "concerts"
    __table_args__ = (UniqueConstraint("source_id", "external_key", name="uq_concert_source_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_key: Mapped[str] = mapped_column(Text)  # per-source concert identity
    date: Mapped[str | None] = mapped_column(Text)  # ISO where derivable
    venue: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(String(50))
    event_type: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship()
    participants: Mapped[list[ConcertParticipant]] = relationship(back_populates="concert")
    works: Mapped[list[ConcertWork]] = relationship(back_populates="concert")


class ConcertParticipant(Base):
    """A participant's role at a concert (conductor | soloist | ensemble).

    ``entity_id`` links to the person entity — or, for an ensemble credit, the
    ensemble entity — when the reported name resolves (by dedup key); the
    verbatim ``name`` is always kept.
    """

    __tablename__ = "concert_participants"
    __table_args__ = (
        Index("ix_concert_participants_entity", "entity_id"),
        Index("ix_concert_participants_concert", "concert_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    concert_id: Mapped[int] = mapped_column(ForeignKey("concerts.id"))
    role: Mapped[str] = mapped_column(String(50))  # conductor | soloist | ensemble
    name: Mapped[str] = mapped_column(Text)
    discipline: Mapped[str | None] = mapped_column(Text)  # soloist instrument/voice
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("entities.id"))

    concert: Mapped[Concert] = relationship(back_populates="participants")


class ConcertWork(Base):
    """A work-performance (mention) that took place at a concert."""

    __tablename__ = "concert_works"
    __table_args__ = (Index("ix_concert_works_concert", "concert_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    concert_id: Mapped[int] = mapped_column(ForeignKey("concerts.id"))
    mention_id: Mapped[int] = mapped_column(ForeignKey("raw_work_mentions.id"))

    concert: Mapped[Concert] = relationship(back_populates="works")
    mention: Mapped[RawWorkMention] = relationship()
