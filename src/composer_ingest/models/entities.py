from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .core import Source


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
