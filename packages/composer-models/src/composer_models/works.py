from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .core import Source
from .entities import Entity


class Work(Base):
    """A canonical musical work.

    Identity is decided by the resolution pipeline (``works/``), not by exact
    title equality, so ``id`` is assigned at creation. The extracted feature
    columns are taken from the title that created the work and are used to score
    future candidate matches.
    """

    __tablename__ = "works"
    __table_args__ = (Index("ix_works_composer", "composer_entity_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    composer_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("entities.id"))
    canonical_title: Mapped[str] = mapped_column(String(500))
    title_key: Mapped[str] = mapped_column(String(500))
    # features extracted from the title, for candidate scoring
    work_type: Mapped[str | None] = mapped_column(String(100))
    opus_number: Mapped[str | None] = mapped_column(String(50))
    catalogue_prefix: Mapped[str | None] = mapped_column(String(20))
    catalogue_number: Mapped[str | None] = mapped_column(String(50))
    musical_key: Mapped[str | None] = mapped_column(String(50))
    number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    composer: Mapped[Entity | None] = relationship()
    titles: Mapped[list[WorkTitle]] = relationship(back_populates="work")


class WorkTitle(Base):
    """A title a work was seen under (an alias), with its source."""

    __tablename__ = "work_titles"
    __table_args__ = (
        UniqueConstraint("work_id", "title_key", "source_id", name="uq_work_title"),
        Index("ix_work_titles_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("works.id"))
    title: Mapped[str] = mapped_column(String(500))
    title_key: Mapped[str] = mapped_column(String(500))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    work: Mapped[Work] = relationship(back_populates="titles")
    source: Mapped[Source] = relationship()


class RawWorkMention(Base):
    """A (composer, title) pair as one source reported it, plus the matcher's
    decision. The raw payload keeps the full performance context (date,
    conductor, soloists, venue) for a later performances pass. Idempotent on
    (source, external_id): an unchanged re-sighting only refreshes
    ``last_seen``, while one whose content actually changed is re-resolved
    against the work catalogue so a corrected title doesn't keep the decision
    made for the old one."""

    __tablename__ = "raw_work_mentions"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_mention_source_external"),
        Index("ix_mentions_status", "match_status"),
        Index("ix_mentions_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(500))
    raw_composer: Mapped[str | None] = mapped_column(String(500))
    raw_title: Mapped[str] = mapped_column(String(500))
    raw: Mapped[str] = mapped_column(Text)  # original payload as JSON
    composer_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("entities.id"))
    work_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("works.id"))
    # unmatched | auto_matched | needs_review | created | manual_matched
    match_status: Mapped[str] = mapped_column(String(20), default="unmatched")
    match_score: Mapped[float | None] = mapped_column(Float)
    match_method: Mapped[str | None] = mapped_column(String(50))
    candidate_work_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("works.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_run_id: Mapped[int] = mapped_column(ForeignKey("ingest_runs.id"))
    last_run_id: Mapped[int] = mapped_column(ForeignKey("ingest_runs.id"))

    source: Mapped[Source] = relationship()
    work: Mapped[Work | None] = relationship(foreign_keys=[work_id])
    candidate_work: Mapped[Work | None] = relationship(foreign_keys=[candidate_work_id])
    composer: Mapped[Entity | None] = relationship()
