import logging
from collections.abc import Iterator

from composer_schema import EntityDocument, WorkMentionDocument
from sqlalchemy.orm import Session

from ..models import IngestRun, utcnow
from .core import IngestError, run_ingest_records
from .entities import get_or_create_source

log = logging.getLogger(__name__)


def create_run(session: Session, source_name: str, base_url: str) -> IngestRun:
    """Register a source and open a ``running`` IngestRun, returning it committed.

    Takes ``source_name``/``base_url`` rather than a ``SourceAdapter`` so the
    admin API can open a run for a crawl config (which has no adapter) too.
    Split out from :func:`ingest_documents` so a caller can record the run and
    learn its id up front, then drive :func:`execute_run` in the background on
    its own session.
    """
    source = get_or_create_source(session, source_name, base_url)
    run = IngestRun(source_id=source.id)
    session.add(run)
    session.commit()
    log.info("run %d started for source '%s'", run.id, source.name)
    return run


def execute_run(
    session: Session,
    run: IngestRun,
    records: Iterator[EntityDocument | WorkMentionDocument],
) -> IngestRun:
    """Ingest ``records`` into the already-created ``run`` and finalize it."""
    source = run.source
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


def ingest_documents(
    session: Session,
    source_name: str,
    base_url: str,
    records: Iterator[EntityDocument | WorkMentionDocument],
) -> IngestRun:
    """Ingest pre-fetched documents (e.g. loaded from a bucket) without network access."""
    source = get_or_create_source(session, source_name, base_url)
    run = IngestRun(source_id=source.id)
    session.add(run)
    session.commit()
    log.info("run %d started for source '%s'", run.id, source.name)
    return execute_run(session, run, records)
