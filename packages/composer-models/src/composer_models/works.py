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
    canonical_title: Mapped[str] = mapped_column(Text)
    title_key: Mapped[str] = mapped_column(Text)
    # features extracted from the title, for candidate scoring
    work_type: Mapped[str | None] = mapped_column(String(100))
    opus_number: Mapped[str | None] = mapped_column(String(50))
    catalogue_prefix: Mapped[str | None] = mapped_column(String(20))
    catalogue_number: Mapped[str | None] = mapped_column(String(50))
    musical_key: Mapped[str | None] = mapped_column(String(50))
    number: Mapped[int | None] = mapped_column(Integer)
    # The world premiere, from whichever source states the earliest date (see
    # composer_schema.premiere); the source that won is kept alongside it.
    premiere_date: Mapped[str | None] = mapped_column(String(50))
    premiere_place: Mapped[str | None] = mapped_column(Text)
    premiere_source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
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
    title: Mapped[str] = mapped_column(Text)
    title_key: Mapped[str] = mapped_column(Text)
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
    external_id: Mapped[str] = mapped_column(Text)
    raw_composer: Mapped[str | None] = mapped_column(Text)
    raw_title: Mapped[str] = mapped_column(Text)
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


class WorkIdentifier(Base):
    """A strong key for a work, as one source stated it: a catalogue number
    (scheme ``BWV``, ``KV``, ``Hob``... -- see ``composer_schema.catalogues``)
    or an external id (``wikidata``, ``musicbrainz``, ``iswc``). The matcher
    tries these before comparing titles."""

    __tablename__ = "work_identifiers"
    __table_args__ = (
        UniqueConstraint("work_id", "scheme", "value", "source_id", name="uq_work_identifier"),
        Index("ix_work_identifiers_scheme_value", "scheme", "value"),
        Index("ix_work_identifiers_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("works.id"))
    scheme: Mapped[str] = mapped_column(String(30))
    # Unbounded in the schema; the writer bounds it to MAX_KEY_CHARS because
    # it sits in the uq_work_identifier btree index.
    value: Mapped[str] = mapped_column(Text)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))

    work: Mapped[Work] = relationship()
    source: Mapped[Source] = relationship()


class WorkLink(Base):
    """A derived work pointing at the work it derives from: ``part_of``,
    ``arrangement_of`` or ``version_of``. Rebuilt wholesale by
    ``composer_warehouse.works.links.link_works``."""

    __tablename__ = "work_links"
    __table_args__ = (
        UniqueConstraint("work_id", "relation", "target_work_id", name="uq_work_link"),
        Index("ix_work_links_target", "target_work_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("works.id"))
    relation: Mapped[str] = mapped_column(String(20))
    target_work_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("works.id"))
    # catalogue | wikidata: what tied the two together
    method: Mapped[str] = mapped_column(String(50))

    work: Mapped[Work] = relationship(foreign_keys=[work_id])
    target: Mapped[Work] = relationship(foreign_keys=[target_work_id])
