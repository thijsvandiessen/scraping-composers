"""Turn the gold database into node and relationship batches.

Pure translation: it reads a gold session and yields plain dicts, so the whole
mapping is testable without a Neo4j instance running. The driver layer
(``writer``) does nothing but send these batches.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from composer_warehouse.models import (
    Claim,
    Concert,
    ConcertParticipant,
    ConcertWork,
    Entity,
    RawWorkMention,
    Recording,
    RecordingParticipant,
    RecordingWork,
    Source,
    Work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ExportConfig
from .model import CONCERT_LABEL, RECORDING_LABEL, WORK_LABEL, entity_label


@dataclass(frozen=True)
class NodeBatch:
    label: str
    rows: list[dict[str, Any]]  # {"id": ..., "props": {...}}


@dataclass(frozen=True)
class RelBatch:
    type: str
    start_label: str
    end_label: str
    rows: list[dict[str, Any]]  # {"start": ..., "end": ..., "props": {...}}


@dataclass
class GoldIndex:
    """The lookups every mapping pass needs, read once.

    Concert and recording ids are *not* stable in gold — ``derive_concerts``
    renumbers them from 1 on every run — so the graph keys them by
    ``source:external_key`` instead, which is the identity the derive pass
    actually grouped on. These maps carry the translation.
    """

    source_names: dict[int, str] = field(default_factory=dict)
    entity_labels: dict[Any, str] = field(default_factory=dict)
    concert_keys: dict[int, str] = field(default_factory=dict)
    recording_keys: dict[int, str] = field(default_factory=dict)
    work_ids: set[Any] = field(default_factory=set)


def _coerce(value: str | None) -> Any:
    """Store a literal as a number when it is one, so Cypher can order by it."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def literal_properties(session: Session, subject_ids: set[Any] | None = None) -> dict[Any, dict[str, Any]]:
    """Literal claims folded into a property map per subject.

    Sources disagree on ~7.6% of birth dates, and a property holds one value, so
    the most-asserted value wins (ties broken by value for determinism). The
    relational gold database remains the place where every source's version of a
    literal is preserved; the graph is for traversal.
    """
    votes: dict[Any, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    stmt = select(Claim.subject_id, Claim.predicate, Claim.value).where(Claim.object_id.is_(None))
    for subject_id, predicate, value in session.execute(stmt).tuples():
        if value is None or (subject_ids is not None and subject_id not in subject_ids):
            continue
        votes[subject_id][predicate][value] += 1

    properties: dict[Any, dict[str, Any]] = {}
    for subject_id, predicates in votes.items():
        properties[subject_id] = {
            predicate: _coerce(max(sorted(counter), key=lambda v: counter[v]))
            for predicate, counter in predicates.items()
        }
    return properties


def build_index(session: Session, config: ExportConfig) -> GoldIndex:
    """Read the id and label lookups the rest of the mapping depends on."""
    index = GoldIndex()
    # dict() would treat the Result as a mapping (it has .keys()); iterate instead.
    index.source_names = {
        source_id: name for source_id, name in session.execute(select(Source.id, Source.name)).tuples()
    }
    index.entity_labels = {
        entity_id: entity_label(kind)
        for entity_id, kind in session.execute(select(Entity.id, Entity.kind)).tuples()
    }
    for concert_id, source_id, external_key in session.execute(
        select(Concert.id, Concert.source_id, Concert.external_key)
    ).tuples():
        index.concert_keys[concert_id] = f"{index.source_names.get(source_id, source_id)}:{external_key}"
    for recording_id, source_id, external_key in session.execute(
        select(Recording.id, Recording.source_id, Recording.external_key)
    ).tuples():
        index.recording_keys[recording_id] = f"{index.source_names.get(source_id, source_id)}:{external_key}"
    index.work_ids = selected_work_ids(session, config)
    return index


def performed_work_ids(session: Session) -> set[Any]:
    """Works that appear on at least one concert or recording programme."""
    concert_works = (
        select(RawWorkMention.work_id)
        .join(ConcertWork, ConcertWork.mention_id == RawWorkMention.id)
        .where(RawWorkMention.work_id.is_not(None))
    )
    recording_works = (
        select(RawWorkMention.work_id)
        .join(RecordingWork, RecordingWork.mention_id == RawWorkMention.id)
        .where(RawWorkMention.work_id.is_not(None))
    )
    return set(session.scalars(concert_works)) | set(session.scalars(recording_works))


def selected_work_ids(session: Session, config: ExportConfig) -> set[Any]:
    if config.include_unperformed_works:
        return set(session.scalars(select(Work.id)))
    return performed_work_ids(session)


def batched(rows: Iterator[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def iter_entity_nodes(session: Session, index: GoldIndex, config: ExportConfig) -> Iterator[NodeBatch]:
    """Entity nodes, one label per kind, carrying their literal claims."""
    properties = literal_properties(session)
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in session.scalars(select(Entity)):
        label = index.entity_labels[entity.id]
        by_label[label].append(
            {
                "id": str(entity.id),
                "props": {"label": entity.label, "kind": entity.kind, **properties.get(entity.id, {})},
            }
        )
    for label, rows in by_label.items():
        for batch in batched(iter(rows), config.batch_size):
            yield NodeBatch(label=label, rows=batch)


def iter_work_nodes(session: Session, index: GoldIndex, config: ExportConfig) -> Iterator[NodeBatch]:
    def rows() -> Iterator[dict[str, Any]]:
        for work in session.scalars(select(Work)):
            if work.id not in index.work_ids:
                continue
            yield {
                "id": str(work.id),
                "props": drop_none(
                    {
                        "title": work.canonical_title,
                        "title_key": work.title_key,
                        "work_type": work.work_type,
                        "opus_number": work.opus_number,
                        "catalogue_prefix": work.catalogue_prefix,
                        "catalogue_number": work.catalogue_number,
                        "musical_key": work.musical_key,
                        "number": work.number,
                    }
                ),
            }

    for batch in batched(rows(), config.batch_size):
        yield NodeBatch(label=WORK_LABEL, rows=batch)


def iter_concert_nodes(session: Session, index: GoldIndex, config: ExportConfig) -> Iterator[NodeBatch]:
    unresolved = _unresolved_by_parent(
        session,
        select(ConcertParticipant.concert_id, ConcertParticipant.name).where(
            ConcertParticipant.entity_id.is_(None)
        ),
    )
    unprogrammed = _unresolved_by_parent(
        session,
        select(ConcertWork.concert_id, RawWorkMention.raw_title)
        .join(RawWorkMention, RawWorkMention.id == ConcertWork.mention_id)
        .where(RawWorkMention.work_id.is_(None)),
    )

    def rows() -> Iterator[dict[str, Any]]:
        for concert in session.scalars(select(Concert)):
            yield {
                "id": index.concert_keys[concert.id],
                "props": drop_none(
                    {
                        "date": concert.date,
                        "venue": concert.venue,
                        "season": concert.season,
                        "event_type": concert.event_type,
                        "url": concert.url,
                        "source": index.source_names.get(concert.source_id),
                        "unresolved_participants": unresolved.get(concert.id),
                        "unprogrammed_titles": unprogrammed.get(concert.id),
                    }
                ),
            }

    for batch in batched(rows(), config.batch_size):
        yield NodeBatch(label=CONCERT_LABEL, rows=batch)


def iter_recording_nodes(session: Session, index: GoldIndex, config: ExportConfig) -> Iterator[NodeBatch]:
    unresolved = _unresolved_by_parent(
        session,
        select(RecordingParticipant.recording_id, RecordingParticipant.name).where(
            RecordingParticipant.entity_id.is_(None)
        ),
    )
    unprogrammed = _unresolved_by_parent(
        session,
        select(RecordingWork.recording_id, RawWorkMention.raw_title)
        .join(RawWorkMention, RawWorkMention.id == RecordingWork.mention_id)
        .where(RawWorkMention.work_id.is_(None)),
    )

    def rows() -> Iterator[dict[str, Any]]:
        for recording in session.scalars(select(Recording)):
            yield {
                "id": index.recording_keys[recording.id],
                "props": drop_none(
                    {
                        "title": recording.title,
                        "release_date": recording.release_date,
                        "label": recording.label,
                        "catalogue_number": recording.catalogue_number,
                        "format": recording.format,
                        "url": recording.url,
                        "source": index.source_names.get(recording.source_id),
                        "unresolved_participants": unresolved.get(recording.id),
                        "unprogrammed_titles": unprogrammed.get(recording.id),
                    }
                ),
            }

    for batch in batched(rows(), config.batch_size):
        yield NodeBatch(label=RECORDING_LABEL, rows=batch)


def _unresolved_by_parent(session: Session, stmt: Any) -> dict[int, list[str]]:
    """Group the verbatim strings a link could not resolve, by parent id.

    A participant or programme entry that resolved to nothing has no node to
    point at. Keeping the raw text as an array property on the event means the
    export loses nothing, rather than quietly dropping ~14% of programme lines.
    """
    grouped: dict[int, list[str]] = defaultdict(list)
    for parent_id, text in session.execute(stmt).tuples():
        if text:
            grouped[parent_id].append(text)
    return grouped


def drop_none(props: dict[str, Any]) -> dict[str, Any]:
    """Neo4j has no null property; absent is the representation."""
    return {key: value for key, value in props.items() if value is not None}
