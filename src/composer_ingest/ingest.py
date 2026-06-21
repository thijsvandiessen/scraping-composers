"""Ingest pipeline: pull records from a source module and upsert them.

Every run is recorded in ``ingest_runs`` (when, which source, how many new
records). Records are idempotent on (source, external_id): re-ingesting
refreshes ``last_seen`` instead of duplicating. New records are linked to a
canonical ``Entity`` via (kind, normalized dedup key), so a second source
ingested later attaches to existing entities instead of creating doubles.
Claims reported by the source ("has_profession", "born_in", ...) are stored
as edges with per-claim provenance; entity objects of claims (professions,
places, ...) are themselves deduplicated entities.

If the source throws mid-run the already-committed batches are preserved;
only the current uncommitted batch is rolled back. The run is marked
"failed" with the error message and timestamp so the next run can try again.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Claim, Entity, EntityRecord, IngestRun, RawWorkMention, Source, Work, WorkTitle, utcnow
from .normalize import dedup_key, entity_uuid
from .sources import SourceLike, SourceRecord, SourceWorkMention
from .works import Candidate, WorkFeatures, extract_features, resolve

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000


class _IngestError(Exception):
    """Wraps a mid-run exception and carries the partial record counts."""

    def __init__(self, cause: Exception, seen: int, new: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.seen = seen
        self.new = new


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


def _flush_entity_timestamps(
    session: Session,
    seen_ids: set[uuid.UUID],
    edited_ids: set[uuid.UUID],
    now: datetime,
) -> None:
    """Bulk-update last_ingested_at / last_edited_at for entities touched in the current batch."""
    if seen_ids:
        session.execute(update(Entity).where(Entity.id.in_(seen_ids)).values(last_ingested_at=now))
    if edited_ids:
        session.execute(update(Entity).where(Entity.id.in_(edited_ids)).values(last_edited_at=now))


def new_work(composer_id: uuid.UUID | None, title: str, features: WorkFeatures) -> Work:
    """A new canonical ``Work`` from a title and its extracted features. The id
    is assigned here (not derived from the title) so the caller can reference it
    before flushing."""
    return Work(
        id=uuid.uuid4(),
        composer_entity_id=composer_id,
        canonical_title=title,
        title_key=features.normalized_title,
        work_type=features.work_type,
        opus_number=features.opus_number,
        catalogue_prefix=features.catalogue_prefix,
        catalogue_number=features.catalogue_number,
        musical_key=features.musical_key,
        number=features.number,
    )


def _ingest_mention(
    session: Session,
    mention: SourceWorkMention,
    source_id: int,
    run_id: int,
    entities_by_key: dict[tuple[str, str], uuid.UUID],
    seen_entity_ids: set[uuid.UUID],
    work_candidates: dict[uuid.UUID | None, list[Candidate]],
    existing_work_titles: set[tuple[uuid.UUID, str]],
) -> int:
    """Resolve one work mention to a canonical work (match/review/create), store
    the mention with the decision, and save its title as an alias. Returns the
    new mention's id."""
    composer_id: uuid.UUID | None = None
    if mention.composer:
        composer_id = _get_or_create_entity(session, entities_by_key, "person", mention.composer)
        seen_entity_ids.add(composer_id)

    features = extract_features(mention.title)
    result = resolve(features, work_candidates.get(composer_id, []))

    matched_work_id = result.work_id
    if result.status == "created":
        work = new_work(composer_id, mention.title, features)
        session.add(work)
        matched_work_id = work.id
        work_candidates.setdefault(composer_id, []).append(Candidate(matched_work_id, features))

    mention_row = RawWorkMention(
        source_id=source_id,
        external_id=mention.external_id,
        raw_composer=mention.composer,
        raw_title=mention.title,
        raw=json.dumps(mention.raw, ensure_ascii=False),
        composer_entity_id=composer_id,
        work_id=matched_work_id,
        match_status=result.status,
        match_score=result.score,
        match_method=result.method,
        candidate_work_id=result.candidate_work_id,
        first_run_id=run_id,
        last_run_id=run_id,
    )
    session.add(mention_row)
    session.flush()

    # save the raw title as an alias of the matched/created work
    if matched_work_id is not None:
        key = (matched_work_id, features.normalized_title)
        if key not in existing_work_titles:
            session.add(
                WorkTitle(
                    work_id=matched_work_id,
                    title=mention.title,
                    title_key=features.normalized_title,
                    source_id=source_id,
                )
            )
            existing_work_titles.add(key)

    return mention_row.id


def _run_ingest_records(
    session: Session,
    source: Source,
    run: IngestRun,
    records_iter: Iterator[SourceRecord | SourceWorkMention],
) -> tuple[int, int]:
    """Core ingest loop shared by run_ingest and run_ingest_from_bucket.

    Returns (records_seen, records_new).
    """
    # Preload existing keys so the per-record loop needs no queries.
    existing_records: dict[str, tuple[int, uuid.UUID | None]] = {
        row[0]: (row[1], row[2])
        for row in session.execute(
            select(EntityRecord.external_id, EntityRecord.id, EntityRecord.entity_id).where(
                EntityRecord.source_id == source.id
            )
        ).tuples()
    }
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
    existing_mentions: dict[str, int] = {
        ext: mid
        for ext, mid in session.execute(
            select(RawWorkMention.external_id, RawWorkMention.id).where(RawWorkMention.source_id == source.id)
        ).tuples()
    }
    existing_work_titles: set[tuple[uuid.UUID, str]] = set(
        session.execute(
            select(WorkTitle.work_id, WorkTitle.title_key).where(WorkTitle.source_id == source.id)
        ).tuples()
    )
    # Candidate works keyed by composer, so the matcher only compares within a
    # composer's catalogue. Refreshed as new works are created during the run.
    work_candidates: dict[uuid.UUID | None, list[Candidate]] = {}
    for work in session.scalars(select(Work)):
        work_candidates.setdefault(work.composer_entity_id, []).append(
            Candidate(work.id, extract_features(work.canonical_title))
        )

    seen = new = 0
    seen_entity_ids: set[uuid.UUID] = set()
    edited_entity_ids: set[uuid.UUID] = set()

    try:
        for item in records_iter:
            seen += 1
            now = utcnow()
            if isinstance(item, SourceWorkMention):
                existing_mid = existing_mentions.get(item.external_id)
                if existing_mid is not None:
                    session.execute(
                        update(RawWorkMention)
                        .where(RawWorkMention.id == existing_mid)
                        .values(last_seen_at=now, last_run_id=run.id)
                    )
                else:
                    existing_mentions[item.external_id] = _ingest_mention(
                        session,
                        item,
                        source.id,
                        run.id,
                        entities_by_key,
                        seen_entity_ids,
                        work_candidates,
                        existing_work_titles,
                    )
                    new += 1
                if seen % COMMIT_BATCH == 0:
                    _flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, now)
                    seen_entity_ids.clear()
                    edited_entity_ids.clear()
                    session.commit()
                    log.info("progress: %d seen, %d new", seen, new)
                continue

            record = item
            existing_entry = existing_records.get(record.external_id)
            if existing_entry is not None:
                existing_id, existing_entity_id = existing_entry
                session.execute(
                    update(EntityRecord)
                    .where(EntityRecord.id == existing_id)
                    .values(last_seen_at=now, last_run_id=run.id)
                )
                if existing_entity_id is not None:
                    seen_entity_ids.add(existing_entity_id)
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
                existing_records[record.external_id] = (db_record.id, entity_id)
                seen_entity_ids.add(entity_id)
                new += 1

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
                    edited_entity_ids.add(entity_id)

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
                        edited_entity_ids.add(entity_id)

            if seen % COMMIT_BATCH == 0:
                _flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, now)
                seen_entity_ids.clear()
                edited_entity_ids.clear()
                session.commit()
                log.info("progress: %d seen, %d new", seen, new)

    except Exception as exc:
        try:
            _flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, utcnow())
            session.commit()
        except Exception:
            session.rollback()
        raise _IngestError(exc, seen, new) from exc

    if seen_entity_ids or edited_entity_ids:
        _flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, utcnow())

    return seen, new


def run_ingest_from_bucket(
    session: Session,
    source_name: str,
    base_url: str,
    records: Iterator[SourceRecord | SourceWorkMention],
) -> IngestRun:
    """Ingest pre-fetched records (loaded from a bucket) without network access."""
    source = _get_or_create_source(session, source_name, base_url)
    run = IngestRun(source_id=source.id)
    session.add(run)
    session.commit()
    log.info("run %d started for source '%s' (from bucket)", run.id, source.name)

    try:
        seen, new = _run_ingest_records(session, source, run, records)
        run.status = "completed"
    except _IngestError as err:
        run.status = "failed"
        run.error = f"{type(err.cause).__name__}: {err.cause}"
        seen, new = err.seen, err.new
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


def run_ingest(session: Session, source_module: SourceLike, max_pages: int | None = None) -> IngestRun:
    source = _get_or_create_source(session, source_module.NAME, source_module.BASE_URL)
    run = IngestRun(source_id=source.id)
    session.add(run)
    session.commit()
    log.info("run %d started for source '%s'", run.id, source.name)

    try:
        seen, new = _run_ingest_records(
            session, source, run, source_module.fetch_records(max_pages=max_pages)
        )
        run.status = "completed"
    except _IngestError as err:
        run.status = "failed"
        run.error = f"{type(err.cause).__name__}: {err.cause}"
        seen, new = err.seen, err.new
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
