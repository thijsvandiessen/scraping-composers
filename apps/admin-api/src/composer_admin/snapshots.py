"""Bucket, snapshot and source-identity plumbing shared across the admin API.

A crawl run reuses the scraper's snapshot machinery wholesale — same bucket,
same manifest states, same running-run guard — so :mod:`.routes`,
:mod:`.crawl_routes` and :mod:`.pipeline` all need these. They lived in
``routes`` and were reached from the other two under their private names; they
are public here instead.
"""

import logging
from datetime import datetime

from composer_bronze.bucket import DEFAULT_BUCKET_PATH, LocalBucket, Snapshot
from composer_bronze.scraper import iter_from_bucket
from composer_crawler import all_crawl_configs
from composer_scrapers import REGISTRY
from composer_warehouse.ingestion import execute_run
from composer_warehouse.models import IngestRun
from fastapi import HTTPException, status

from .deps import session_scope
from .logconfig import safe_for_log
from .schemas import SnapshotOut

log = logging.getLogger(__name__)


def source_base_url(source: str) -> str:
    """Base URL for a bucket source: a registered scraper, or a crawl config's
    first seed. Mirrors the CLI's ``_source_identity`` so crawl-config sources
    (whose LLM ``extract`` docs live under their name) can open an IngestRun."""
    adapter = REGISTRY.get(source)
    if adapter is not None:
        return adapter.base_url
    config = all_crawl_configs().get(source)
    return config.seeds[0] if config and config.seeds else ""


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
    422 like ``put_crawl`` does rather than the 500 an unhandled ValueError gives.
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


def process_in_background(source_name: str, snapshot_id: str, run_id: int) -> None:
    """Load a bucket snapshot into the DB for an already-created run.

    ``source_name`` may be a scraper or a crawl config — the bucket records are
    the same ``entity``/``work_mention`` documents either way.
    """
    with session_scope() as session:
        run = session.get(IngestRun, run_id)
        if run is not None:
            execute_run(session, run, iter_from_bucket(source_name, snapshot_id, bucket()))


def running_conflict(name: str) -> HTTPException:
    """The 409 every endpoint raises when a source already has work in flight."""
    return HTTPException(status.HTTP_409_CONFLICT, f"a run for {name!r} is already in progress")
