import json
import logging
import re
import uuid
from collections.abc import Iterator

from composer_schema import EntityDocument, WorkMentionDocument
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Claim, Entity, EntityRecord, IngestRun, RawWorkMention, Source, Work, WorkTitle, utcnow
from ..works import Candidate, extract_features
from .entities import flush_entity_timestamps, get_or_create_entity
from .mentions import ingest_mention

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000


def _extract_wikidata_id(url: str | None) -> str | None:
    if not url:
        return None
    if match := re.search(r"wikidata\.org/wiki/(Q\d+)", url):
        return match.group(1)
    return None


class IngestError(Exception):
    """Wraps a mid-run exception and carries the partial record counts."""

    def __init__(self, cause: Exception, seen: int, new: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.seen = seen
        self.new = new


def run_ingest_records(
    session: Session,
    source: Source,
    run: IngestRun,
    records_iter: Iterator[EntityDocument | WorkMentionDocument],
) -> tuple[int, int]:
    """Core ingest loop driven by execute_run / ingest_documents.

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
            if isinstance(item, WorkMentionDocument):
                existing_mid = existing_mentions.get(item.id)
                if existing_mid is not None:
                    session.execute(
                        update(RawWorkMention)
                        .where(RawWorkMention.id == existing_mid)
                        .values(last_seen_at=now, last_run_id=run.id)
                    )
                else:
                    existing_mentions[item.id] = ingest_mention(
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
                    flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, now)
                    seen_entity_ids.clear()
                    edited_entity_ids.clear()
                    session.commit()
                    log.info("progress: %d seen, %d new", seen, new)
                continue

            record = item
            existing_entry = existing_records.get(record.id)
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
                wikidata_id = _extract_wikidata_id(record.url)
                entity_id = get_or_create_entity(
                    session, entities_by_key, record.kind, record.name, wikidata_id
                )
                db_record = EntityRecord(
                    source_id=source.id,
                    entity_id=entity_id,
                    external_id=record.id,
                    name=record.name,
                    url=record.url,
                    raw=json.dumps(record.raw, ensure_ascii=False),
                    first_run_id=run.id,
                    last_run_id=run.id,
                )
                session.add(db_record)
                session.flush()
                existing_records[record.id] = (db_record.id, entity_id)
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
                        get_or_create_entity(session, entities_by_key, claim.object_kind, claim.object_label)
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
                flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, now)
                seen_entity_ids.clear()
                edited_entity_ids.clear()
                session.commit()
                log.info("progress: %d seen, %d new", seen, new)

    except Exception as exc:
        try:
            flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, utcnow())
            session.commit()
        except Exception:
            session.rollback()
        raise IngestError(exc, seen, new) from exc

    if seen_entity_ids or edited_entity_ids:
        flush_entity_timestamps(session, seen_entity_ids, edited_entity_ids, utcnow())

    return seen, new
