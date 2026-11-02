"""A work's instrumentation, read from the source entities linked to it.

``composer_warehouse.works.entities`` links each canonical work to the ``work``
entities its sources emitted (``work_entities``); those carry the
``written_for`` and ``includes_instrument`` claims, and their records the
parts with counts. Two adapters write parts today, under the one shape this
reads: Bärenreiter (``stated``, ``instrument``, ``count``, ``solo``) and
``wikidata_instrumentation`` (``label`` in place of ``stated``).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from composer_models import Claim, Entity, EntityRecord, Source, WorkEntity
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..schemas import ScoringInstrumentOut, ScoringPartOut, ScoringSourceOut, WorkScoringOut
from .common import deep_link

_PREDICATES = {"written_for": "written_for", "includes_instrument": "includes"}


def _instruments(rows: dict[uuid.UUID, tuple[str, set[str]]]) -> list[ScoringInstrumentOut]:
    return [
        ScoringInstrumentOut(id=entity_id, label=label, sources=sorted(sources))
        for entity_id, (label, sources) in sorted(rows.items(), key=lambda kv: (kv[1][0], str(kv[0])))
    ]


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parts(raw: str) -> list[ScoringPartOut]:
    """The parts a record's ``raw["instrumentation"]`` lists, or none."""
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        return []
    instrumentation = payload.get("instrumentation") if isinstance(payload, dict) else None
    parts = instrumentation.get("parts") if isinstance(instrumentation, dict) else None
    if not isinstance(parts, list):
        return []
    return [
        ScoringPartOut(
            stated=_text(part.get("stated")) or _text(part.get("label")),
            instrument=_text(part.get("instrument")),
            count=_count(part.get("count")),
            solo=part.get("solo") is True,
        )
        for part in parts  # pyright: ignore[reportUnknownVariableType]
        if isinstance(part, dict)
    ]


def work_scoring(db: Session, work_id: uuid.UUID) -> WorkScoringOut:
    """The work's instrumentation categories, each with the sources stating it,
    and each source's part list (identical lists from one source, as two
    editions of one work give, shown once)."""
    links = db.execute(
        select(WorkEntity.entity_id, WorkEntity.source_id).where(WorkEntity.work_id == work_id)
    ).all()
    if not links:
        return WorkScoringOut()
    entity_ids = sorted({entity_id for entity_id, _ in links}, key=str)

    found: dict[str, dict[uuid.UUID, tuple[str, set[str]]]] = {"written_for": {}, "includes": {}}
    for predicate, object_id, label, source in db.execute(
        select(Claim.predicate, Claim.object_id, Entity.label, Source.name)
        .join(Entity, Entity.id == Claim.object_id)
        .join(Source, Source.id == Claim.source_id)
        .where(Claim.subject_id.in_(entity_ids), Claim.predicate.in_(_PREDICATES))
    ):
        if object_id is None:
            continue
        rows = found[_PREDICATES[predicate]]
        rows.setdefault(object_id, (label, set()))[1].add(source)

    pairs = {(entity_id, source_id) for entity_id, source_id in links}
    sources: list[ScoringSourceOut] = []
    seen: set[tuple[str, str]] = set()
    for entity_id, source_id, url, raw, source, base_url in db.execute(
        select(
            EntityRecord.entity_id,
            EntityRecord.source_id,
            EntityRecord.url,
            EntityRecord.raw,
            Source.name,
            Source.base_url,
        )
        .join(Source, Source.id == EntityRecord.source_id)
        .where(EntityRecord.entity_id.in_(entity_ids))
        .order_by(Source.name, EntityRecord.id)
    ):
        if (entity_id, source_id) not in pairs or not (parts := _parts(raw)):
            continue
        key = (source, json.dumps([part.model_dump() for part in parts]))
        if key in seen:
            continue
        seen.add(key)
        sources.append(ScoringSourceOut(source=source, source_url=deep_link(url, base_url), parts=parts))

    return WorkScoringOut(
        written_for=_instruments(found["written_for"]),
        includes=_instruments(found["includes"]),
        sources=sources,
    )
