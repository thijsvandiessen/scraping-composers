"""Ingest pipeline: pull records from a source module and upsert them.

Every run is recorded in ``ingest_runs`` (when, which source, how many new
records). Records are idempotent on (source, external_id): re-ingesting
refreshes ``last_seen`` instead of duplicating. New records are linked to a
canonical ``Entity`` via (kind, normalized dedup key), so a second source
ingested later attaches to existing entities instead of creating doubles.
Claims reported by the source ("has_profession", "born_in", ...) are stored
as edges with per-claim provenance; entity objects of claims (professions,
places, ...) are themselves deduplicated entities.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Claim, Entity, EntityRecord, IngestRun, Source, utcnow
from .normalize import dedup_key, entity_uuid
from .sources import SourceLike

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000


def _get_or_create_source(session: Session, name: str, base_url: str) -> Source:
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None:
        source = Source(name=name, base_url=base_url)
        session.add(source)
        session.flush()
    return source


def _get_or_create_entity(
    session: Session, cache: dict[tuple[str, str], uuid.UUID], kind: str, label: str
) -> uuid.UUID:
    key = dedup_key(label)
    entity_id = cache.get((kind, key))
    if entity_id is None:
        entity_id = entity_uuid(kind, key)
        session.add(Entity(id=entity_id, kind=kind, dedup_key=key, label=label))
        cache[(kind, key)] = entity_id
    return entity_id


def run_ingest(session: Session, source_module: SourceLike, max_pages: int | None = None) -> IngestRun:
    source = _get_or_create_source(session, source_module.NAME, source_module.BASE_URL)
    run = IngestRun(source_id=source.id)
    session.add(run)
    session.commit()
    log.info("run %d started for source '%s'", run.id, source.name)

    # Preload existing keys so the per-record loop needs no queries.
    # .all() first: dict() would otherwise treat the Result (which has .keys())
    # as a mapping and subscript it
    existing_records: dict[str, int] = dict(
        session.execute(
            select(EntityRecord.external_id, EntityRecord.id).where(EntityRecord.source_id == source.id)
        )
        .tuples()
        .all()
    )
    entities_by_key: dict[tuple[str, str], uuid.UUID] = {
        (kind, key): entity_id
        for kind, key, entity_id in session.execute(select(Entity.kind, Entity.dedup_key, Entity.id)).tuples()
    }
    existing_claims: set[tuple[uuid.UUID, str, uuid.UUID | None, str | None]] = set(
        session.execute(
            select(Claim.subject_id, Claim.predicate, Claim.object_id, Claim.value).where(
                Claim.source_id == source.id
            )
        ).tuples()
    )

    seen = new = 0
    try:
        for record in source_module.fetch_records(max_pages=max_pages):
            seen += 1
            now = utcnow()
            existing_id = existing_records.get(record.external_id)
            if existing_id is not None:
                session.execute(
                    update(EntityRecord)
                    .where(EntityRecord.id == existing_id)
                    .values(last_seen_at=now, last_run_id=run.id)
                )
            else:
                entity_id = _get_or_create_entity(session, entities_by_key, record.kind, record.name)
                db_record = EntityRecord(
                    source_id=source.id,
                    entity_id=entity_id,
                    external_id=record.external_id,
                    name=record.name,
                    url=record.url,
                    raw=json.dumps(record.raw, ensure_ascii=False),
                    first_run_id=run.id,
                    last_run_id=run.id,
                )
                session.add(db_record)
                session.flush()
                existing_records[record.external_id] = db_record.id
                new += 1

                # Auto-inject a "mentioned_in" claim so every entity carries
                # a reference back to the source page where it was found.
                mention_url = record.url or source.base_url
                mention_key = (entity_id, "mentioned_in", None, mention_url)
                if mention_key not in existing_claims:
                    session.add(
                        Claim(
                            subject_id=entity_id,
                            predicate="mentioned_in",
                            value=mention_url,
                            source_id=source.id,
                            record_id=db_record.id,
                        )
                    )
                    existing_claims.add(mention_key)

                for claim in record.claims:
                    object_id = (
                        _get_or_create_entity(session, entities_by_key, claim.object_kind, claim.object_label)
                        if claim.object_kind is not None and claim.object_label is not None
                        else None
                    )
                    claim_key = (entity_id, claim.predicate, object_id, claim.value)
                    if claim_key not in existing_claims:
                        session.add(
                            Claim(
                                subject_id=entity_id,
                                predicate=claim.predicate,
                                object_id=object_id,
                                value=claim.value,
                                source_id=source.id,
                                record_id=db_record.id,
                            )
                        )
                        existing_claims.add(claim_key)

            if seen % COMMIT_BATCH == 0:
                session.commit()
                log.info("progress: %d seen, %d new", seen, new)

        run.status = "completed"
    except Exception as exc:
        session.rollback()
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        log.exception("run %d failed after %d records", run.id, seen)

    run.records_seen = seen
    run.records_new = new
    run.finished_at = utcnow()
    session.commit()
    log.info(
        "run %d %s: %d records seen, %d new (source '%s')",
        run.id,
        run.status,
        seen,
        new,
        source.name,
    )
    return run
