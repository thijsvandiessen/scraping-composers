"""Per-run ingest state: preloaded caches and progress counters.

All lookups the per-record loop needs are loaded up front so ingesting a
record touches no queries; the caches are kept in sync as rows are added.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Claim, Entity, EntityRecord, IngestRun, RawWorkMention, Source, Work, WorkTitle
from ..works import Candidate, extract_features


@dataclass
class IngestState:
    session: Session
    source: Source
    run: IngestRun
    # external_id -> (record id, entity id) for this source
    existing_records: dict[str, tuple[int, uuid.UUID | None]]
    # (kind, dedup key) -> entity id, across all sources
    entities_by_key: dict[tuple[str, str], uuid.UUID]
    # (subject, predicate, object, value) claims already stored for this source
    existing_claims: set[tuple[uuid.UUID, str, uuid.UUID | None, str | None]]
    # external_id -> mention id for this source
    existing_mentions: dict[str, int]
    # (work id, title key) aliases already stored for this source
    existing_work_titles: set[tuple[uuid.UUID, str]]
    # Candidate works keyed by composer, so the matcher only compares within a
    # composer's catalogue. Refreshed as new works are created during the run.
    work_candidates: dict[uuid.UUID | None, list[Candidate]]
    seen: int = 0
    new: int = 0
    seen_entity_ids: set[uuid.UUID] = field(default_factory=set)
    edited_entity_ids: set[uuid.UUID] = field(default_factory=set)


def load_state(session: Session, source: Source, run: IngestRun) -> IngestState:
    """Preload existing keys so the per-record loop needs no queries."""
    existing_records = {
        row[0]: (row[1], row[2])
        for row in session.execute(
            select(EntityRecord.external_id, EntityRecord.id, EntityRecord.entity_id).where(
                EntityRecord.source_id == source.id
            )
        ).tuples()
    }
    entities_by_key = {
        (kind, key): entity_id
        for kind, key, entity_id in session.execute(select(Entity.kind, Entity.dedup_key, Entity.id)).tuples()
    }
    existing_claims = set(
        session.execute(
            select(Claim.subject_id, Claim.predicate, Claim.object_id, Claim.value).where(
                Claim.source_id == source.id
            )
        ).tuples()
    )
    existing_mentions = {
        ext: mid
        for ext, mid in session.execute(
            select(RawWorkMention.external_id, RawWorkMention.id).where(RawWorkMention.source_id == source.id)
        ).tuples()
    }
    existing_work_titles = set(
        session.execute(
            select(WorkTitle.work_id, WorkTitle.title_key).where(WorkTitle.source_id == source.id)
        ).tuples()
    )
    work_candidates: dict[uuid.UUID | None, list[Candidate]] = {}
    for work in session.scalars(select(Work)):
        work_candidates.setdefault(work.composer_entity_id, []).append(
            Candidate(work.id, extract_features(work.canonical_title))
        )

    return IngestState(
        session=session,
        source=source,
        run=run,
        existing_records=existing_records,
        entities_by_key=entities_by_key,
        existing_claims=existing_claims,
        existing_mentions=existing_mentions,
        existing_work_titles=existing_work_titles,
        work_candidates=work_candidates,
    )
