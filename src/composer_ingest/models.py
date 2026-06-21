"""Database schema.

Design notes:
- ``EntityRecord`` stores the raw record exactly as a source delivered it,
  keyed by (source, external_id). Re-running an ingest updates ``last_seen``
  instead of duplicating rows. This is the raw staging layer: nothing is
  curated here, curation happens downstream in the golden index.
- ``Entity`` is the canonical, deduplicated node. ``kind`` says what it is:
  "person", "profession", "period", "genre", "place", "work" (open set —
  sources may introduce more). Records from different sources that normalize
  to the same (kind, dedup_key) link to the same entity.
- ``Claim`` connects entities with typed edges, e.g.
  person --has_profession--> profession, person --born_in--> place,
  person --composed--> work. The object is either another entity
  (``object_id``) or a literal (``value``, e.g. a date string). Every claim
  carries the source and the raw record it was extracted from, so conflicting
  sources coexist instead of overwriting each other.
- ``IngestRun`` is the collection log: when data was collected, from which
  source, and how much of it was new.
- ``Work`` is a canonical musical work. Unlike ``Entity``, works are not
  deduplicated by exact normalized title (the same work appears as "Symphony
  No. 5 in C minor", "Sinfonie Nr. 5 c-moll", "Beethoven's Fifth", ...): a work
  resolution pipeline (``works/``) matches each ``RawWorkMention`` to an existing
  work or creates a new one, so a work's ``id`` is assigned at creation, not
  derived from its title. ``WorkTitle`` records every title a work was seen
  under (its aliases). ``RawWorkMention`` is the raw staging row for a
  (composer, title) pair as a source reported it, plus the matcher's decision.
- ``Entity.canonical_entity_id`` links a duplicate person to its canonical
  entity (non-destructive); ``PersonMatch`` is the dedupe pass's review queue,
  recording each proposed (duplicate, canonical) pair and its decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    runs: Mapped[list[IngestRun]] = relationship(back_populates="source")


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|failed
    records_seen: Mapped[int] = mapped_column(default=0)
    records_new: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="runs")


class Entity(Base):
    """Canonical node, deduplicated across sources within its kind."""

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("kind", "dedup_key", name="uq_entity_kind_key"),
        Index("ix_entities_canonical", "canonical_entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    dedup_key: Mapped[str] = mapped_column(String(300))
    label: Mapped[str] = mapped_column(String(300))
    # set when this entity is a confirmed duplicate of another (the canonical
    # one), e.g. "Beethoven" -> "Beethoven, Ludwig van". Non-destructive: both
    # rows stay, queries can resolve to the canonical via this pointer.
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("entities.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    records: Mapped[list[EntityRecord]] = relationship(back_populates="entity")
    canonical: Mapped[Entity | None] = relationship(remote_side=[id])


class EntityRecord(Base):
    """An entity as reported by one specific source, with full provenance."""

    __tablename__ = "entity_records"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_source_external"),
        Index("ix_entity_records_entity_id", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("entities.id"))
    external_id: Mapped[str] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(500))
    raw: Mapped[str] = mapped_column(Text)  # original payload as JSON
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_run_id: Mapped[int] = mapped_column(ForeignKey("ingest_runs.id"))
    last_run_id: Mapped[int] = mapped_column(ForeignKey("ingest_runs.id"))

    entity: Mapped[Entity | None] = relationship(back_populates="records")
    source: Mapped[Source] = relationship()


class Claim(Base):
    """A typed edge between entities, as asserted by one source.

    Exactly one of ``object_id`` (entity object) or ``value`` (literal, e.g.
    a date string) is set. The same fact asserted by two sources is two
    claims — provenance is per claim, conflicts are resolved downstream.
    """

    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claims_subject_predicate", "subject_id", "predicate"),
        Index("ix_claims_predicate_object", "predicate", "object_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("entities.id"))
    predicate: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("entities.id"))
    value: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    record_id: Mapped[int | None] = mapped_column(ForeignKey("entity_records.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subject: Mapped[Entity] = relationship(foreign_keys=[subject_id])
    object: Mapped[Entity | None] = relationship(foreign_keys=[object_id])
    source: Mapped[Source] = relationship()
    record: Mapped[EntityRecord | None] = relationship()


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
    (source, external_id): re-ingesting refreshes ``last_seen`` instead of
    re-resolving."""

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


class PersonMatch(Base):
    """A proposed (duplicate -> canonical) person pair and the dedupe pass's
    decision. ``auto_linked`` / ``accepted`` also set ``entity.canonical_entity_id``;
    ``needs_review`` awaits a human; ``rejected`` is remembered so re-runs don't
    re-propose. One row per ordered pair."""

    __tablename__ = "person_matches"
    __table_args__ = (
        UniqueConstraint("entity_id", "canonical_entity_id", name="uq_person_match"),
        Index("ix_person_matches_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("entities.id"))
    canonical_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("entities.id"))
    score: Mapped[float] = mapped_column(Float)
    method: Mapped[str | None] = mapped_column(String(50))
    # auto_linked | needs_review | accepted | rejected
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entity: Mapped[Entity] = relationship(foreign_keys=[entity_id])
    canonical: Mapped[Entity] = relationship(foreign_keys=[canonical_entity_id])
