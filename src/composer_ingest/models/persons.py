from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .entities import Entity


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
