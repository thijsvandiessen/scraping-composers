# pylint: disable=too-many-lines
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from composer_bronze.bucket import DEFAULT_BUCKET_PATH, LOADABLE_STATUSES, LocalBucket, Snapshot
from composer_bronze.scraper import Scraper, iter_from_bucket, new_snapshot_id
from composer_gold import (
    DEFAULT_GOLD_DB_PATH,
    DEFAULT_MIN_SITELINKS,
    PromoteConfig,
    promote,
    read_gold_manifest,
)
from composer_scrapers import REGISTRY, SourceAdapter, is_due
from composer_warehouse.build import read_build_manifest
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.ingestion import create_run, execute_run
from composer_warehouse.models import IngestRun, utcnow
from composer_warehouse.rebuild import rebuild_silver, sqlite_db_path
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from .crud import get_run, has_running, list_runs
from .deps import DbSession, dispose_db, require_admin_key, session_scope
from .schemas import (
    FetchStarted,
    GoldStatus,
    PromoteOptions,
    RunOut,
    RunStarted,
    ScraperOut,
    SilverStatus,
    SnapshotOut,
)

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
        started_at=m.started_at,
        finished_at=m.finished_at,
        record_count=m.record_count,
        size_bytes=snapshot.size_bytes,
        error=m.error,
    )


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
    """Load a bucket snapshot into the DB for an already-created run."""
    adapter = REGISTRY[source_name]
    with session_scope() as session:
        run = session.get(IngestRun, run_id)
        if run is not None:
            execute_run(session, run, iter_from_bucket(adapter.name, snapshot_id, _bucket()))


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
    """Every raw snapshot in the bucket, newest first."""
    bucket = _bucket()
    snapshots = [_snapshot_out(s) for name in REGISTRY for s in bucket.list_snapshots(name)]
    return sorted(snapshots, key=lambda s: s.id, reverse=True)


@admin.post(
    "/snapshots/{source}/{snapshot_id}/process",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunStarted,
)
def process_snapshot(source: str, snapshot_id: str, db: DbSession, background: BackgroundTasks) -> RunStarted:
    """Load a raw snapshot from the bucket into the database (background)."""
    adapter = REGISTRY.get(source)
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown scraper {source!r}")
    bucket = _bucket()
    snapshot = next((s for s in bucket.list_snapshots(source) if s.manifest.run_id == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown snapshot {source}/{snapshot_id}")
    if snapshot.manifest.status not in LOADABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"snapshot {source}/{snapshot_id} is not loadable (status: {snapshot.manifest.status})",
        )
    if has_running(db, source):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a run for {source!r} is already in progress")
    run = create_run(db, adapter)
    background.add_task(_process_in_background, source, snapshot_id, run.id)
    return RunStarted(run_id=run.id, source=source, status=run.status)


def _promote_in_background(gold_path: str, config: PromoteConfig) -> None:
    """Rebuild the gold database; status lives in the gold manifest."""
    with session_scope() as session:
        try:
            # Concerts are silver-derived state the gold build copies; refresh
            # them first so the Promote button never publishes stale concerts.
            derive_concerts(session)
            promote(session, gold_path, config)
        except Exception:
            # Recorded as a failed manifest by promote; log for the server console.
            log.exception("background promote failed")


def _promote_config(options: PromoteOptions | None) -> tuple[str, PromoteConfig]:
    """Resolve the request body (or its absence) into a gold path and config.

    ``min_sitelinks`` left out of the body falls back to the configured
    default; an explicit ``null`` switches the sitelink signal off.
    """
    opts = options or PromoteOptions()
    gold_path = opts.gold_path or DEFAULT_GOLD_DB_PATH
    min_sitelinks = opts.min_sitelinks if "min_sitelinks" in opts.model_fields_set else DEFAULT_MIN_SITELINKS
    config = PromoteConfig(
        min_sitelinks=min_sitelinks,
        drop_unevidenced_persons=opts.drop_unevidenced_persons,
        collapse_duplicates=opts.collapse_duplicates,
        prune_unreferenced=opts.prune_unreferenced,
    )
    return str(gold_path), config


def _gold_status(gold_path: str | None = None) -> GoldStatus:
    path = gold_path or DEFAULT_GOLD_DB_PATH
    manifest = read_gold_manifest(path)
    return GoldStatus(
        exists=Path(path).exists(),
        status=manifest.status if manifest else None,
        started_at=manifest.started_at if manifest else None,
        finished_at=manifest.finished_at if manifest else None,
        error=manifest.error if manifest else None,
        stats=manifest.stats if manifest else {},
    )


@admin.get("/gold", response_model=GoldStatus)
def gold_status() -> GoldStatus:
    """State of the gold database: last promote, its stats, current activity."""
    return _gold_status()


@admin.post("/promote", status_code=status.HTTP_202_ACCEPTED, response_model=GoldStatus)
def start_promote(background: BackgroundTasks, options: PromoteOptions | None = None) -> GoldStatus:
    """Rebuild the curated gold database from silver (background).

    The optional body tunes the run (see ``PromoteOptions``); a bodiless POST
    runs the full curation with the configured defaults.
    """
    gold_path, config = _promote_config(options)
    manifest = read_gold_manifest(gold_path)
    if manifest is not None and manifest.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "a promote is already in progress")
    background.add_task(_promote_in_background, gold_path, config)
    current = _gold_status(gold_path)
    current.status = "running"
    return current


def _silver_db_path() -> Path | None:
    """The silver database file, or None when DATABASE_URL isn't sqlite."""
    from composer_config import settings

    try:
        return sqlite_db_path(settings.database_url)
    except ValueError:
        return None


def _rebuild_silver_in_background() -> None:
    """Rebuild silver from the bucket; status lives in the silver manifest."""
    sources = [(adapter.name, adapter.base_url) for adapter in REGISTRY.values()]
    try:
        rebuild_silver(_bucket(), sources)
    except Exception:
        # Recorded as a failed manifest by rebuild_silver; log for the console.
        log.exception("background silver rebuild failed")
    finally:
        # The swap replaced the database file; drop pooled connections to it.
        dispose_db()


def _silver_status() -> SilverStatus:
    path = _silver_db_path()
    manifest = read_build_manifest(path) if path is not None else None
    return SilverStatus(
        exists=path.exists() if path is not None else False,
        status=manifest.status if manifest else None,
        started_at=manifest.started_at if manifest else None,
        finished_at=manifest.finished_at if manifest else None,
        error=manifest.error if manifest else None,
        stats=manifest.stats if manifest else {},
    )


@admin.get("/silver", response_model=SilverStatus)
def silver_status() -> SilverStatus:
    """State of the silver database: last rebuild, its stats, current activity."""
    return _silver_status()


@admin.post("/rebuild-silver", status_code=status.HTTP_202_ACCEPTED, response_model=SilverStatus)
def start_rebuild_silver(background: BackgroundTasks) -> SilverStatus:
    """Rebuild the silver database from the bucket (background)."""
    path = _silver_db_path()
    if path is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "rebuild-silver requires a file-backed sqlite DATABASE_URL",
        )
    manifest = read_build_manifest(path)
    if manifest is not None and manifest.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "a silver rebuild is already in progress")
    background.add_task(_rebuild_silver_in_background)
    current = _silver_status()
    current.status = "running"
    return current


@admin.get("/runs", response_model=list[RunOut])
def get_runs(db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[RunOut]:
    return list_runs(db, limit)


@admin.get("/runs/{run_id}", response_model=RunOut)
def get_single_run(run_id: int, db: DbSession) -> RunOut:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown run {run_id}")
    return run
