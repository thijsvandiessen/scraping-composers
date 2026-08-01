import logging
from dataclasses import replace
from datetime import datetime
from typing import Annotated

from composer_bronze.bucket import DEFAULT_BUCKET_PATH, LOADABLE_STATUSES, LocalBucket, Snapshot
from composer_bronze.scraper import Scraper, iter_from_bucket, new_snapshot_id
from composer_crawler import all_crawl_configs
from composer_scrapers import REGISTRY, SourceAdapter, is_due
from composer_warehouse.ingestion import create_run, execute_run
from composer_warehouse.models import IngestRun, utcnow
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from .crud import get_run, has_running, list_runs
from .deps import DbSession, require_admin_key, session_scope
from .logconfig import safe_for_log
from .schemas import FetchStarted, RunOut, RunStarted, ScraperOut, SnapshotOut

log = logging.getLogger(__name__)

admin = APIRouter(prefix="/admin/v1", dependencies=[Depends(require_admin_key)])


def _bucket() -> LocalBucket:
    return LocalBucket(DEFAULT_BUCKET_PATH)


def _snapshot_out(snapshot: Snapshot) -> SnapshotOut:
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


def _source_base_url(source: str) -> str:
    """Base URL for a bucket source: a registered scraper, or a crawl config's
    first seed. Mirrors the CLI's ``_source_identity`` so crawl-config sources
    (whose LLM ``extract`` docs live under their name) can open an IngestRun."""
    adapter = REGISTRY.get(source)
    if adapter is not None:
        return adapter.base_url
    config = all_crawl_configs().get(source)
    return config.seeds[0] if config and config.seeds else ""


def _last_snapshot(bucket: LocalBucket, source: str) -> Snapshot | None:
    snapshots = bucket.list_snapshots(source)
    return snapshots[-1] if snapshots else None


def _has_running_fetch(bucket: LocalBucket, source: str) -> bool:
    return any(s.manifest.status == "running" for s in bucket.list_snapshots(source))


def _fetch_in_background(source_name: str, snapshot_id: str, max_pages: int | None) -> None:
    """Fetch a source to the bucket; status lives in the snapshot's manifest."""
    adapter = REGISTRY[source_name]
    try:
        Scraper(adapter).fetch_to_bucket(_bucket(), max_pages=max_pages, run_id=snapshot_id)
    except Exception:
        # Recorded as a failed manifest by fetch_to_bucket; log for the server console.
        log.exception("background fetch failed for %s/%s", source_name, snapshot_id)


def _process_in_background(source_name: str, snapshot_id: str, run_id: int) -> None:
    """Load a bucket snapshot into the DB for an already-created run.

    ``source_name`` may be a scraper or a crawl config — the bucket records are
    the same ``entity``/``work_mention`` documents either way.
    """
    with session_scope() as session:
        run = session.get(IngestRun, run_id)
        if run is not None:
            execute_run(session, run, iter_from_bucket(source_name, snapshot_id, _bucket()))


def _start_fetch(background: BackgroundTasks, adapter: SourceAdapter, max_pages: int | None) -> FetchStarted:
    snapshot_id = new_snapshot_id()
    background.add_task(_fetch_in_background, adapter.name, snapshot_id, max_pages)
    return FetchStarted(source=adapter.name, snapshot_id=snapshot_id, status="running")


def _scraper_out(adapter: SourceAdapter, last: Snapshot | None, now: datetime) -> ScraperOut:
    last_started = None
    if last is not None and last.manifest.started_at:
        last_started = datetime.fromisoformat(last.manifest.started_at)
    return ScraperOut(
        name=adapter.name,
        base_url=adapter.base_url,
        cadence=adapter.cadence.value,
        due=is_due(adapter.cadence, last_started, now),
        last_snapshot=_snapshot_out(last) if last is not None else None,
    )


@admin.get("/scrapers", response_model=list[ScraperOut])
def list_scrapers() -> list[ScraperOut]:
    now = utcnow()
    bucket = _bucket()
    return [_scraper_out(adapter, _last_snapshot(bucket, name), now) for name, adapter in REGISTRY.items()]


@admin.get("/scrapers/{name}", response_model=ScraperOut)
def get_scraper(name: str) -> ScraperOut:
    adapter = REGISTRY.get(name)
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown scraper {name!r}")
    return _scraper_out(adapter, _last_snapshot(_bucket(), name), utcnow())


@admin.post("/scrapers/fetch-due", response_model=list[FetchStarted])
def fetch_due(background: BackgroundTasks) -> list[FetchStarted]:
    """Start a background fetch for every scraper whose raw data is stale."""
    now = utcnow()
    bucket = _bucket()
    started: list[FetchStarted] = []
    for name, adapter in REGISTRY.items():
        last = _last_snapshot(bucket, name)
        last_started = None
        if last is not None and last.manifest.started_at:
            last_started = datetime.fromisoformat(last.manifest.started_at)
        if _has_running_fetch(bucket, name) or not is_due(adapter.cadence, last_started, now):
            continue
        started.append(_start_fetch(background, adapter, None))
    return started


@admin.post("/scrapers/{name}/fetch", status_code=status.HTTP_202_ACCEPTED, response_model=FetchStarted)
def fetch_scraper(
    name: str,
    background: BackgroundTasks,
    max_pages: Annotated[int | None, Query(ge=1)] = None,
) -> FetchStarted:
    adapter = REGISTRY.get(name)
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown scraper {name!r}")
    if _has_running_fetch(_bucket(), name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a fetch for {name!r} is already in progress")
    return _start_fetch(background, adapter, max_pages)


@admin.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots() -> list[SnapshotOut]:
    """Every snapshot in the bucket, newest first.

    Enumerates the bucket's own sources (not just ``REGISTRY``), so crawl-config
    sources and their LLM-extracted ``documents`` snapshots show up too.
    """
    bucket = _bucket()
    snapshots = [_snapshot_out(s) for name in bucket.list_sources() for s in bucket.list_snapshots(name)]
    return sorted(snapshots, key=lambda s: s.id, reverse=True)


@admin.post("/snapshots/{source}/{snapshot_id}/abandon", response_model=SnapshotOut)
def abandon_snapshot(source: str, snapshot_id: str) -> SnapshotOut:
    """Mark a stuck ``running`` snapshot failed, unblocking the source.

    A fetch or crawl killed outright (the process gone, honcho stopped) never
    gets to finalize its manifest, so it stays ``running`` forever: the
    dashboard shows it as live and ``_has_running_fetch`` refuses to start
    anything new for that source. This is the way out — nothing is deleted, the
    pages already written stay readable, and ``record_count`` is corrected to
    what is actually on disk.

    Whether the run is really dead is the caller's judgement: a crawl that *is*
    still going will carry on writing to a snapshot now marked failed.
    """
    bucket = _bucket()
    snapshot = next((s for s in bucket.list_snapshots(source) if s.manifest.run_id == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown snapshot {source}/{snapshot_id}")
    if snapshot.manifest.status != "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"snapshot {source}/{snapshot_id} is not running (status: {snapshot.manifest.status})",
        )
    on_disk = sum(1 for _ in bucket.read_records(source, snapshot_id))
    manifest = snapshot.manifest.failed("abandoned: the run was not finished by its process", on_disk)
    bucket.write_manifest(manifest)
    log.info(
        "abandoned stale snapshot %s/%s (%d record(s) on disk)",
        safe_for_log(source),
        safe_for_log(snapshot_id),
        on_disk,
    )
    return _snapshot_out(replace(snapshot, manifest=manifest))


@admin.post(
    "/snapshots/{source}/{snapshot_id}/process",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunStarted,
)
def process_snapshot(source: str, snapshot_id: str, db: DbSession, background: BackgroundTasks) -> RunStarted:
    """Load a snapshot's documents from the bucket into the database (background).

    Works for scraper and crawl-config sources alike; the latter's loadable
    snapshots are the ``documents`` the LLM ``extract`` step wrote.
    """
    bucket = _bucket()
    snapshot = next((s for s in bucket.list_snapshots(source) if s.manifest.run_id == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown snapshot {source}/{snapshot_id}")
    if snapshot.manifest.status not in LOADABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"snapshot {source}/{snapshot_id} is not loadable (status: {snapshot.manifest.status})",
        )
    if snapshot.kind != "documents":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"snapshot {source}/{snapshot_id} holds crawled pages, not documents; run extract first",
        )
    if has_running(db, source):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a run for {source!r} is already in progress")
    run = create_run(db, source, _source_base_url(source))
    background.add_task(_process_in_background, source, snapshot_id, run.id)
    return RunStarted(run_id=run.id, source=source, status=run.status)


@admin.get("/runs", response_model=list[RunOut])
def get_runs(db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[RunOut]:
    return list_runs(db, limit)


@admin.get("/runs/{run_id}", response_model=RunOut)
def get_single_run(run_id: int, db: DbSession) -> RunOut:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown run {run_id}")
    return run
