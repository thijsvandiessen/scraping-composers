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
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
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
    __table_args__ = (UniqueConstraint("kind", "dedup_key", name="uq_entity_kind_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    dedup_key: Mapped[str] = mapped_column(String(300))
    label: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    records: Mapped[list[EntityRecord]] = relationship(back_populates="entity")


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
