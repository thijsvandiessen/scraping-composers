"""Copy phases of the gold build: kept rows, re-pointed at canonical roots."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from composer_models import (
    Concert,
    ConcertParticipant,
    ConcertWork,
    Entity,
    EntityRecord,
    IngestRun,
    RawWorkMention,
    Recording,
    RecordingParticipant,
    RecordingWork,
    Source,
    Work,
    WorkTitle,
)
from sqlalchemy import Connection, insert, select

from ._selection import GoldBuild

INSERT_BATCH = 1000
# SQLite limits the number of bound variables; chunk large IN () lists.
IN_CHUNK = 500


def chunked(ids: list[Any]) -> Iterable[list[Any]]:
    for i in range(0, len(ids), IN_CHUNK):
        yield ids[i : i + IN_CHUNK]


def _entity_row(e: Entity) -> dict[str, Any]:
    """An entity insert row with the canonical link resolved away."""
    return {
        "id": e.id,
        "kind": e.kind,
        "dedup_key": e.dedup_key,
        "label": e.label,
        "canonical_entity_id": None,
        "created_at": e.created_at,
        "first_ingested_at": e.first_ingested_at,
        "last_ingested_at": e.last_ingested_at,
        "last_edited_at": e.last_edited_at,
    }


def copy_sources_and_runs(build: GoldBuild, gold: Connection) -> None:
    """FK targets: sources and runs, wholesale."""
    for row in build.silver.execute(select(Source)).scalars():
        gold.execute(
            insert(Source).values(id=row.id, name=row.name, base_url=row.base_url, created_at=row.created_at)
        )
    run_rows = [
        {
            "id": r.id,
            "source_id": r.source_id,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "status": r.status,
            "records_seen": r.records_seen,
            "records_new": r.records_new,
            "error": r.error,
        }
        for r in build.silver.execute(select(IngestRun)).scalars()
    ]
    if run_rows:
        gold.execute(insert(IngestRun), run_rows)


def copy_entities(build: GoldBuild, gold: Connection, ids: set[uuid.UUID]) -> None:
    for chunk in chunked(sorted(ids, key=str)):
        rows = [
            _entity_row(e) for e in build.silver.execute(select(Entity).where(Entity.id.in_(chunk))).scalars()
        ]
        if rows:
            gold.execute(insert(Entity), rows)


def copy_records(build: GoldBuild, gold: Connection) -> None:
    """Entity records of everything kept, re-pointed."""
    record_owner_ids = sorted(build.kept_members | build.kept_other, key=str)
    for chunk in chunked(record_owner_ids):
        rows = [
            {
                "id": r.id,
                "source_id": r.source_id,
                "entity_id": build.root(r.entity_id) if r.entity_id is not None else None,
                "external_id": r.external_id,
                "name": r.name,
                "url": r.url,
                "raw": r.raw,
                "first_seen_at": r.first_seen_at,
                "last_seen_at": r.last_seen_at,
                "first_run_id": r.first_run_id,
                "last_run_id": r.last_run_id,
            }
            for r in build.silver.execute(
                select(EntityRecord).where(EntityRecord.entity_id.in_(chunk))
            ).scalars()
        ]
        if rows:
            gold.execute(insert(EntityRecord), rows)
            build.record_count += len(rows)


def copy_works_titles_mentions(build: GoldBuild, gold: Connection) -> None:
    """Works, titles, mentions — composer ids remapped."""
    work_rows = [
        {
            "id": w.id,
            "composer_entity_id": build.root(w.composer_entity_id) if w.composer_entity_id else None,
            "canonical_title": w.canonical_title,
            "title_key": w.title_key,
            "work_type": w.work_type,
            "opus_number": w.opus_number,
            "catalogue_prefix": w.catalogue_prefix,
            "catalogue_number": w.catalogue_number,
            "musical_key": w.musical_key,
            "number": w.number,
            "created_at": w.created_at,
            "first_ingested_at": w.first_ingested_at,
            "last_ingested_at": w.last_ingested_at,
        }
        for w in build.silver.execute(select(Work)).scalars()
    ]
    for i in range(0, len(work_rows), INSERT_BATCH):
        gold.execute(insert(Work), work_rows[i : i + INSERT_BATCH])
    build.work_count = len(work_rows)

    title_rows = [
        {
            "id": t.id,
            "work_id": t.work_id,
            "title": t.title,
            "title_key": t.title_key,
            "source_id": t.source_id,
            "first_seen_at": t.first_seen_at,
        }
        for t in build.silver.execute(select(WorkTitle)).scalars()
    ]
    for i in range(0, len(title_rows), INSERT_BATCH):
        gold.execute(insert(WorkTitle), title_rows[i : i + INSERT_BATCH])
    build.title_count = len(title_rows)

    mention_rows = [
        {
            "id": m.id,
            "source_id": m.source_id,
            "external_id": m.external_id,
            "raw_composer": m.raw_composer,
            "raw_title": m.raw_title,
            "raw": m.raw,
            "composer_entity_id": build.root(m.composer_entity_id) if m.composer_entity_id else None,
            "work_id": m.work_id,
            "match_status": m.match_status,
            "match_score": m.match_score,
            "match_method": m.match_method,
            "candidate_work_id": m.candidate_work_id,
            "first_seen_at": m.first_seen_at,
            "last_seen_at": m.last_seen_at,
            "first_run_id": m.first_run_id,
            "last_run_id": m.last_run_id,
        }
        for m in build.silver.execute(select(RawWorkMention)).scalars()
    ]
    for i in range(0, len(mention_rows), INSERT_BATCH):
        gold.execute(insert(RawWorkMention), mention_rows[i : i + INSERT_BATCH])
    build.mention_count = len(mention_rows)


def copy_concerts(build: GoldBuild, gold: Connection) -> None:
    """Concerts: copy the silver-derived tables, re-pointing people.

    ``derive_concerts`` resolved participants against every person entity;
    here duplicates collapse to their canonical root, and links to persons
    that didn't make it into gold are nulled (the verbatim name is always
    kept)."""
    gold_entities = build.kept_roots | build.kept_other
    concert_rows = [
        {
            "id": c.id,
            "source_id": c.source_id,
            "external_key": c.external_key,
            "date": c.date,
            "venue": c.venue,
            "season": c.season,
            "event_type": c.event_type,
            "url": c.url,
        }
        for c in build.silver.execute(select(Concert)).scalars()
    ]
    participant_rows: list[dict[str, Any]] = []
    for p in build.silver.execute(select(ConcertParticipant)).scalars():
        entity_id = build.root(p.entity_id) if p.entity_id is not None else None
        if entity_id is not None and entity_id not in gold_entities:
            entity_id = None
        if entity_id is not None:
            build.participant_links += 1
        else:
            build.unresolved_names.add(p.name)
        participant_rows.append(
            {
                "concert_id": p.concert_id,
                "role": p.role,
                "name": p.name,
                "discipline": p.discipline,
                "entity_id": entity_id,
            }
        )
    concert_work_rows = [
        {"concert_id": cw.concert_id, "mention_id": cw.mention_id}
        for cw in build.silver.execute(select(ConcertWork)).scalars()
    ]

    for i in range(0, len(concert_rows), INSERT_BATCH):
        gold.execute(insert(Concert), concert_rows[i : i + INSERT_BATCH])
    for i in range(0, len(participant_rows), INSERT_BATCH):
        gold.execute(insert(ConcertParticipant), participant_rows[i : i + INSERT_BATCH])
    for i in range(0, len(concert_work_rows), INSERT_BATCH):
        gold.execute(insert(ConcertWork), concert_work_rows[i : i + INSERT_BATCH])
    build.concert_count = len(concert_rows)


def copy_recordings(build: GoldBuild, gold: Connection) -> None:
    """Recordings: copy the silver-derived tables, re-pointing people.

    The album counterpart to ``copy_concerts``: ``derive_recordings`` resolved
    participants against every person entity; here duplicates collapse to their
    canonical root, and links to persons that didn't make it into gold are
    nulled (the verbatim name is always kept)."""
    gold_entities = build.kept_roots | build.kept_other
    recording_rows = [
        {
            "id": r.id,
            "source_id": r.source_id,
            "external_key": r.external_key,
            "title": r.title,
            "release_date": r.release_date,
            "label": r.label,
            "catalogue_number": r.catalogue_number,
            "format": r.format,
            "url": r.url,
        }
        for r in build.silver.execute(select(Recording)).scalars()
    ]
    participant_rows: list[dict[str, Any]] = []
    for p in build.silver.execute(select(RecordingParticipant)).scalars():
        entity_id = build.root(p.entity_id) if p.entity_id is not None else None
        if entity_id is not None and entity_id not in gold_entities:
            entity_id = None
        if entity_id is not None:
            build.recording_participant_links += 1
        else:
            build.recording_unresolved_names.add(p.name)
        participant_rows.append(
            {
                "recording_id": p.recording_id,
                "role": p.role,
                "name": p.name,
                "discipline": p.discipline,
                "entity_id": entity_id,
            }
        )
    recording_work_rows = [
        {"recording_id": rw.recording_id, "mention_id": rw.mention_id}
        for rw in build.silver.execute(select(RecordingWork)).scalars()
    ]

    for i in range(0, len(recording_rows), INSERT_BATCH):
        gold.execute(insert(Recording), recording_rows[i : i + INSERT_BATCH])
    for i in range(0, len(participant_rows), INSERT_BATCH):
        gold.execute(insert(RecordingParticipant), participant_rows[i : i + INSERT_BATCH])
    for i in range(0, len(recording_work_rows), INSERT_BATCH):
        gold.execute(insert(RecordingWork), recording_work_rows[i : i + INSERT_BATCH])
    build.recording_count = len(recording_rows)
