import json
import logging
import re
import uuid
from collections.abc import Iterator
from datetime import datetime

from composer_schema import EntityDocument, WorkMentionDocument
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models import Claim, EntityRecord, IngestRun, RawWorkMention, Source, utcnow
from .entities import flush_entity_timestamps, get_or_create_entity
from .mentions import ingest_mention
from .state import IngestState, load_state

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


def _ingest_work_mention(state: IngestState, item: WorkMentionDocument, now: datetime) -> None:
    existing_mid = state.existing_mentions.get(item.id)
    if existing_mid is not None:
        state.session.execute(
            update(RawWorkMention)
            .where(RawWorkMention.id == existing_mid)
            .values(last_seen_at=now, last_run_id=state.run.id)
        )
    else:
        state.existing_mentions[item.id] = ingest_mention(state, item)
        state.new += 1


def _add_record_claims(
    state: IngestState, record: EntityDocument, db_record: EntityRecord, entity_id: uuid.UUID
) -> None:
    """Store the record's claims (plus a ``mentioned_in`` provenance claim),
    skipping any already present for this source."""
    mention_url = record.url or state.source.base_url
    mention_key = (entity_id, "mentioned_in", None, mention_url)
    if mention_key not in state.existing_claims:
        state.session.add(
            Claim(
                subject_id=entity_id,
                predicate="mentioned_in",
                value=mention_url,
                source_id=state.source.id,
                record_id=db_record.id,
            )
        )
        state.existing_claims.add(mention_key)
        state.edited_entity_ids.add(entity_id)

    for claim in record.claims:
        object_id = (
            get_or_create_entity(state.session, state.entities_by_key, claim.object_kind, claim.object_label)
            if claim.object_kind is not None and claim.object_label is not None
            else None
        )
        claim_key = (entity_id, claim.predicate, object_id, claim.value)
        if claim_key not in state.existing_claims:
            state.session.add(
                Claim(
                    subject_id=entity_id,
                    predicate=claim.predicate,
                    object_id=object_id,
                    value=claim.value,
                    source_id=state.source.id,
                    record_id=db_record.id,
                )
            )
            state.existing_claims.add(claim_key)
            state.edited_entity_ids.add(entity_id)


def _create_entity_record(state: IngestState, record: EntityDocument) -> None:
    wikidata_id = _extract_wikidata_id(record.url)
    entity_id = get_or_create_entity(
        state.session, state.entities_by_key, record.kind, record.name, wikidata_id
    )
    db_record = EntityRecord(
        source_id=state.source.id,
        entity_id=entity_id,
        external_id=record.id,
        name=record.name,
        url=record.url,
        raw=json.dumps(record.raw, ensure_ascii=False),
        first_run_id=state.run.id,
        last_run_id=state.run.id,
    )
    state.session.add(db_record)
    state.session.flush()
    state.existing_records[record.id] = (db_record.id, entity_id)
    state.seen_entity_ids.add(entity_id)
    state.new += 1
    _add_record_claims(state, record, db_record, entity_id)


def _ingest_entity_record(state: IngestState, record: EntityDocument, now: datetime) -> None:
    existing_entry = state.existing_records.get(record.id)
    if existing_entry is None:
        _create_entity_record(state, record)
        return
    existing_id, existing_entity_id = existing_entry
    state.session.execute(
        update(EntityRecord)
        .where(EntityRecord.id == existing_id)
        .values(last_seen_at=now, last_run_id=state.run.id)
    )
    if existing_entity_id is not None:
        state.seen_entity_ids.add(existing_entity_id)


def _flush_batch(state: IngestState, now: datetime) -> None:
    flush_entity_timestamps(state.session, state.seen_entity_ids, state.edited_entity_ids, now)
    state.seen_entity_ids.clear()
    state.edited_entity_ids.clear()
    state.session.commit()
    log.info("progress: %d seen, %d new", state.seen, state.new)


def run_ingest_records(
    session: Session,
    source: Source,
    run: IngestRun,
    records_iter: Iterator[EntityDocument | WorkMentionDocument],
) -> tuple[int, int]:
    """Core ingest loop driven by execute_run / ingest_documents.

    Returns (records_seen, records_new).
    """
    state = load_state(session, source, run)
    try:
        for item in records_iter:
            state.seen += 1
            now = utcnow()
            if isinstance(item, WorkMentionDocument):
                _ingest_work_mention(state, item, now)
            else:
                _ingest_entity_record(state, item, now)
            if state.seen % COMMIT_BATCH == 0:
                _flush_batch(state, now)
    except Exception as exc:
        try:
            flush_entity_timestamps(session, state.seen_entity_ids, state.edited_entity_ids, utcnow())
            session.commit()
        except Exception:
            session.rollback()
        raise IngestError(exc, state.seen, state.new) from exc

    if state.seen_entity_ids or state.edited_entity_ids:
        flush_entity_timestamps(session, state.seen_entity_ids, state.edited_entity_ids, utcnow())

    return state.seen, state.new
