"""Bucket, snapshot and source-identity plumbing shared across the admin API.

Everything that fetches, lists or loads a bucket snapshot needs these.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from composer_bronze.bucket import DEFAULT_BUCKET_PATH, LocalBucket, Snapshot
from composer_bronze.scraper import iter_all_from_bucket, iter_from_bucket
from composer_models import IngestRun, utcnow
from composer_scrapers import source_base_url as source_base_url
from composer_warehouse.ingestion import execute_run
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .deps import session_scope
from .logconfig import safe_for_log
from .schemas import SnapshotOut

log = logging.getLogger(__name__)


def bucket() -> LocalBucket:
    return LocalBucket(DEFAULT_BUCKET_PATH)


def snapshot_out(snapshot: Snapshot) -> SnapshotOut:
    m = snapshot.manifest
    return SnapshotOut(
        source=m.source,
        id=m.run_id,
        status=m.status,
        kind=snapshot.kind,
        started_at=m.started_at,
        finished_at=m.finished_at,
        record_count=m.record_count,
        size_bytes=snapshot.size_bytes,
        error=m.error,
    )


def last_snapshot(store: LocalBucket, source: str) -> Snapshot | None:
    snapshots = store.list_snapshots(source)
    return snapshots[-1] if snapshots else None


def last_started(snapshot: Snapshot | None) -> datetime | None:
    """When a snapshot's run began, for the staleness check — None if it never did."""
    if snapshot is None or not snapshot.manifest.started_at:
        return None
    return datetime.fromisoformat(snapshot.manifest.started_at)


def has_running_fetch(store: LocalBucket, source: str) -> bool:
    return any(s.manifest.status == "running" for s in store.list_snapshots(source))


def snapshot_or_404(store: LocalBucket, source: str, snapshot_id: str) -> Snapshot:
    """One snapshot by source and run_id, as the endpoints that act on it need it.

    ``list_snapshots`` raises when the bucket's segment guard refuses *source*
    (traversal, control characters). That is a malformed identifier, so it answers
    422 rather than the 500 an unhandled ValueError gives.
    """
    try:
        snapshots = store.list_snapshots(source)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    snapshot = next((s for s in snapshots if s.manifest.run_id == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"unknown snapshot {safe_for_log(source)}/{safe_for_log(snapshot_id)}",
        )
    return snapshot


def _run_background_ingest(run_id: int, label: str, load: Callable[[Session, IngestRun], IngestRun]) -> None:
    """Execute a background ingest run and guarantee the run row is finalized on error."""
    with session_scope() as session:
        run = session.get(IngestRun, run_id)
        if run is None:
            return
        try:
            load(session, run)
        except Exception as exc:
            # execute_run only catches IngestError; an unexpected I/O or DB error
            # would otherwise leave the run stuck on "running" forever.
            log.exception("background process failed for %s (run %d)", label, run_id)
            session.rollback()
            stale = session.get(IngestRun, run_id)
            if stale is not None and stale.status == "running":
                stale.status = "failed"
                stale.error = f"{type(exc).__name__}: {exc}"
                stale.finished_at = utcnow()
                session.commit()


def process_in_background(source_name: str, snapshot_id: str, run_id: int) -> None:
    """Load a bucket snapshot into the DB for an already-created run.

    The bucket records are ``entity``/``work_mention`` documents.
    """
    _run_background_ingest(
        run_id,
        f"{source_name}/{snapshot_id}",
        lambda session, run: execute_run(session, run, iter_from_bucket(source_name, snapshot_id, bucket())),
    )


def process_all_in_background(source_name: str, snapshot_ids: Sequence[str], run_id: int) -> None:
    """Load every snapshot in ``snapshot_ids`` into the DB for an already-created run.

    Like :func:`process_in_background` but unions every loadable run instead
    of one: ingestion is idempotent per external_id, so replaying every run
    (oldest to newest) captures every unique record ever seen for the source
    rather than whatever the single latest run happened to contain.
    """
    _run_background_ingest(
        run_id,
        source_name,
        lambda session, run: execute_run(
            session, run, iter_all_from_bucket(source_name, snapshot_ids, bucket())
        ),
    )


def running_conflict(name: str) -> HTTPException:
    """The 409 every endpoint raises when a source already has work in flight."""
    return HTTPException(status.HTTP_409_CONFLICT, f"a run for {name!r} is already in progress")
