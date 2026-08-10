"""Relationship batches: the edges of the exported graph.

Batches are grouped by ``(type, start label, end label)`` because that is what
lets the writer's ``MATCH`` use the per-label uniqueness constraint on both ends
instead of scanning.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from composer_warehouse.models import (
    Claim,
    ConcertParticipant,
    ConcertWork,
    RawWorkMention,
    RecordingParticipant,
    RecordingWork,
    Work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ExportConfig
from .mapping import GoldIndex, NodeBatch, RelBatch, batched, drop_none
from .model import (
    COMPOSED_BY,
    CONCERT_LABEL,
    CONTAINS,
    PROGRAMMES,
    RECORDING_LABEL,
    WORK_LABEL,
    claim_relationship,
    participant_relationship,
)

RelKey = tuple[str, str, str]  # (type, start label, end label)


def _emit(grouped: dict[RelKey, list[dict[str, Any]]], config: ExportConfig) -> Iterator[RelBatch]:
    for (rel_type, start_label, end_label), rows in grouped.items():
        for batch in batched(iter(rows), config.batch_size):
            yield RelBatch(type=rel_type, start_label=start_label, end_label=end_label, rows=batch)


def iter_claim_relationships(
    session: Session, index: GoldIndex, config: ExportConfig
) -> Iterator[RelBatch]:
    """Object claims: the facts a source asserted linking two entities."""
    grouped: dict[RelKey, list[dict[str, Any]]] = defaultdict(list)
    stmt = select(Claim.subject_id, Claim.predicate, Claim.object_id, Claim.source_id).where(
        Claim.object_id.is_not(None)
    )
    seen: set[tuple[Any, str, Any]] = set()
    for subject_id, predicate, object_id, source_id in session.execute(stmt).tuples():
        start_label = index.entity_labels.get(subject_id)
        end_label = index.entity_labels.get(object_id)
        if start_label is None or end_label is None:
            continue
        # The same fact from two sources is two claim rows but one edge; keep
        # the first source seen so the relationship count stays predictable.
        key = (subject_id, predicate, object_id)
        if key in seen:
            continue
        seen.add(key)
        grouped[(claim_relationship(predicate), start_label, end_label)].append(
            {
                "start": str(subject_id),
                "end": str(object_id),
                "props": drop_none({"source": index.source_names.get(source_id)}),
            }
        )
    yield from _emit(grouped, config)


def iter_composer_relationships(
    session: Session, index: GoldIndex, config: ExportConfig
) -> Iterator[RelBatch]:
    """``(:Work)-[:COMPOSED_BY]->(:Person)`` for every exported work."""
    grouped: dict[RelKey, list[dict[str, Any]]] = defaultdict(list)
    stmt = select(Work.id, Work.composer_entity_id).where(Work.composer_entity_id.is_not(None))
    for work_id, composer_id in session.execute(stmt).tuples():
        if work_id not in index.work_ids:
            continue
        end_label = index.entity_labels.get(composer_id)
        if end_label is None:
            continue
        grouped[(COMPOSED_BY, WORK_LABEL, end_label)].append(
            {"start": str(work_id), "end": str(composer_id), "props": {}}
        )
    yield from _emit(grouped, config)


def iter_participant_relationships(
    session: Session, index: GoldIndex, config: ExportConfig
) -> Iterator[RelBatch]:
    """Concert and recording credits — one relationship family for both.

    This is where the relational schema's concert/recording duplication stops
    being duplication: the two participant tables differ only in which column
    names the event, and here they differ only in the start label.
    """
    grouped: dict[RelKey, list[dict[str, Any]]] = defaultdict(list)
    sources: list[tuple[Any, Any, str, dict[int, str]]] = [
        (ConcertParticipant, ConcertParticipant.concert_id, CONCERT_LABEL, index.concert_keys),
        (RecordingParticipant, RecordingParticipant.recording_id, RECORDING_LABEL, index.recording_keys),
    ]
    for model, parent_column, start_label, keys in sources:
        stmt = select(
            parent_column, model.role, model.name, model.discipline, model.entity_id
        ).where(model.entity_id.is_not(None))
        for parent_id, role, name, discipline, entity_id in session.execute(stmt).tuples():
            end_label = index.entity_labels.get(entity_id)
            start = keys.get(parent_id)
            if end_label is None or start is None:
                continue
            grouped[(participant_relationship(role), start_label, end_label)].append(
                {
                    "start": start,
                    "end": str(entity_id),
                    "props": drop_none({"name": name, "discipline": discipline, "role": role}),
                }
            )
    yield from _emit(grouped, config)


def iter_programme_relationships(
    session: Session, index: GoldIndex, config: ExportConfig
) -> Iterator[RelBatch]:
    """What was played: concert → work, and recording → work.

    Programme entries whose mention never resolved to a work have no endpoint;
    they are kept verbatim as the event's ``unprogrammed_titles`` property (see
    ``mapping``), so nothing is silently dropped here.
    """
    grouped: dict[RelKey, list[dict[str, Any]]] = defaultdict(list)
    plans: list[tuple[Any, Any, Any, str, str, dict[int, str]]] = [
        (ConcertWork, ConcertWork.concert_id, ConcertWork.id, PROGRAMMES, CONCERT_LABEL, index.concert_keys),
        (
            RecordingWork,
            RecordingWork.recording_id,
            RecordingWork.id,
            CONTAINS,
            RECORDING_LABEL,
            index.recording_keys,
        ),
    ]
    for model, parent_column, order_column, rel_type, start_label, keys in plans:
        stmt = (
            select(
                parent_column,
                RawWorkMention.work_id,
                RawWorkMention.raw_title,
                RawWorkMention.raw_composer,
                RawWorkMention.source_id,
            )
            .join(RawWorkMention, RawWorkMention.id == model.mention_id)
            .where(RawWorkMention.work_id.is_not(None))
            .order_by(order_column)
        )
        position: dict[Any, int] = defaultdict(int)
        for parent_id, work_id, raw_title, raw_composer, source_id in session.execute(stmt).tuples():
            start = keys.get(parent_id)
            if start is None or work_id not in index.work_ids:
                continue
            position[parent_id] += 1
            grouped[(rel_type, start_label, WORK_LABEL)].append(
                {
                    "start": start,
                    "end": str(work_id),
                    "props": drop_none(
                        {
                            "raw_title": raw_title,
                            "raw_composer": raw_composer,
                            "position": position[parent_id],
                            "source": index.source_names.get(source_id),
                        }
                    ),
                }
            )
    yield from _emit(grouped, config)


__all__ = [
    "NodeBatch",
    "RelBatch",
    "iter_claim_relationships",
    "iter_composer_relationships",
    "iter_participant_relationships",
    "iter_programme_relationships",
]
