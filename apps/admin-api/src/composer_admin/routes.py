import logging
from dataclasses import replace
from datetime import datetime
from typing import Annotated

from composer_bronze.bucket import EXPLICITLY_LOADABLE_STATUSES, Snapshot
from composer_bronze.scraper import Scraper, new_snapshot_id
from composer_models import utcnow
from composer_scrapers import REGISTRY, SourceAdapter, is_due
from composer_warehouse.ingestion import create_run
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from .crud import get_run, has_running, list_runs
from .deps import DbSession, require_admin_key
from .logconfig import safe_for_log
from .schemas import FetchStarted, RunOut, RunStarted, ScraperOut, SnapshotOut
from .snapshots import (
    bucket,
    has_running_fetch,
    last_snapshot,
    last_started,
    process_in_background,
    running_conflict,
    snapshot_or_404,
    snapshot_out,
    source_base_url,
)

log = logging.getLogger(__name__)

admin = APIRouter(prefix="/admin/v1", dependencies=[Depends(require_admin_key)])


def _fetch_in_background(source_name: str, snapshot_id: str, max_pages: int | None) -> None:
    """Fetch a source to the bucket; status lives in the snapshot's manifest."""
    adapter = REGISTRY[source_name]
    try:
        Scraper(adapter).fetch_to_bucket(bucket(), max_pages=max_pages, run_id=snapshot_id)
    except Exception:
        # Recorded as a failed manifest by fetch_to_bucket; log for the server console.
        log.exception("background fetch failed for %s/%s", source_name, snapshot_id)


def _start_fetch(background: BackgroundTasks, adapter: SourceAdapter, max_pages: int | None) -> FetchStarted:
    snapshot_id = new_snapshot_id()
    background.add_task(_fetch_in_background, adapter.name, snapshot_id, max_pages)
    return FetchStarted(source=adapter.name, snapshot_id=snapshot_id, status="running")


def _scraper_out(adapter: SourceAdapter, last: Snapshot | None, now: datetime) -> ScraperOut:
    return ScraperOut(
        name=adapter.name,
        base_url=adapter.base_url,
        cadence=adapter.cadence.value,
        due=is_due(adapter.cadence, last_started(last), now),
        last_snapshot=snapshot_out(last) if last is not None else None,
    )


@admin.get("/scrapers", response_model=list[ScraperOut])
def list_scrapers() -> list[ScraperOut]:
    now = utcnow()
    store = bucket()
    return [_scraper_out(adapter, last_snapshot(store, name), now) for name, adapter in REGISTRY.items()]


@admin.get("/scrapers/{name}", response_model=ScraperOut)
def get_scraper(name: str) -> ScraperOut:
    adapter = REGISTRY.get(name)
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown scraper {name!r}")
    return _scraper_out(adapter, last_snapshot(bucket(), name), utcnow())


@admin.post("/scrapers/fetch-due", response_model=list[FetchStarted])
def fetch_due(background: BackgroundTasks) -> list[FetchStarted]:
    """Start a background fetch for every scraper whose raw data is stale."""
    now = utcnow()
    store = bucket()
    started: list[FetchStarted] = []
    for name, adapter in REGISTRY.items():
        last = last_snapshot(store, name)
        if has_running_fetch(store, name) or not is_due(adapter.cadence, last_started(last), now):
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
    if has_running_fetch(bucket(), name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a fetch for {name!r} is already in progress")
    return _start_fetch(background, adapter, max_pages)


@admin.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots() -> list[SnapshotOut]:
    """Every snapshot in the bucket, newest first.

    Enumerates the bucket's own sources (not just ``REGISTRY``), so crawl-config
    sources and their LLM-extracted ``documents`` snapshots show up too.
    """
    store = bucket()
    snapshots = [snapshot_out(s) for name in store.list_sources() for s in store.list_snapshots(name)]
    return sorted(snapshots, key=lambda s: s.id, reverse=True)


@admin.post("/snapshots/{source}/{snapshot_id}/abandon", response_model=SnapshotOut)
def abandon_snapshot(source: str, snapshot_id: str) -> SnapshotOut:
    """Mark a stuck ``running`` snapshot failed, unblocking the source.

    A fetch or crawl killed outright (the process gone, honcho stopped) never
    gets to finalize its manifest, so it stays ``running`` forever: the
    dashboard shows it as live and ``has_running_fetch`` refuses to start
    anything new for that source. This is the way out — nothing is deleted, the
    pages already written stay readable, and ``record_count`` is corrected to
    what is actually on disk.

    Whether the run is really dead is the caller's judgement: a crawl that *is*
    still going will carry on writing to a snapshot now marked failed.
    """
    store = bucket()
    snapshot = snapshot_or_404(store, source, snapshot_id)
    if snapshot.manifest.status != "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"snapshot {source}/{snapshot_id} is not running (status: {snapshot.manifest.status})",
        )
    on_disk = sum(1 for _ in store.read_records(source, snapshot_id))
    manifest = snapshot.manifest.failed("abandoned: the run was not finished by its process", on_disk)
    store.write_manifest(manifest)
    log.info(
        "abandoned stale snapshot %s/%s (%d record(s) on disk)",
        safe_for_log(source),
        safe_for_log(snapshot_id),
        on_disk,
    )
    return snapshot_out(replace(snapshot, manifest=manifest))


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
    snapshot = snapshot_or_404(bucket(), source, snapshot_id)
    if snapshot.manifest.status not in EXPLICITLY_LOADABLE_STATUSES:
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
        raise running_conflict(source)
    run = create_run(db, source, source_base_url(source))
    background.add_task(process_in_background, source, snapshot_id, run.id)
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
