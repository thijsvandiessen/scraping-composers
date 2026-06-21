import logging
from collections.abc import Iterator

from sqlalchemy.orm import Session

from ...scraper.sources import SourceLike, SourceRecord, SourceWorkMention
from ..models import IngestRun, utcnow
from .core import IngestError, run_ingest_records
from .entities import get_or_create_source

log = logging.getLogger(__name__)


def run_ingest_from_bucket(
    session: Session,
    source_name: str,
    base_url: str,
    records: Iterator[SourceRecord | SourceWorkMention],
) -> IngestRun:
    """Ingest pre-fetched records (loaded from a bucket) without network access."""
    source = get_or_create_source(session, source_name, base_url)
    run = IngestRun(source_id=source.id)
    session.add(run)
    session.commit()
    log.info("run %d started for source '%s' (from bucket)", run.id, source.name)

    try:
        seen, new = run_ingest_records(session, source, run, records)
        run.status = "completed"
    except IngestError as err:
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
    source = get_or_create_source(session, source_module.NAME, source_module.BASE_URL)
    run = IngestRun(source_id=source.id)
    session.add(run)
    session.commit()
    log.info("run %d started for source '%s'", run.id, source.name)

    try:
        seen, new = run_ingest_records(session, source, run, source_module.fetch_records(max_pages=max_pages))
        run.status = "completed"
    except IngestError as err:
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
