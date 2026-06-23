from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from ..etl.ingestion import create_run, execute_run
from ..etl.models import IngestRun, utcnow
from ..scraper.sources import REGISTRY, SourceAdapter, is_due
from .crud import get_run, has_running, last_run_per_source, list_runs
from .deps import DbSession, require_admin_key, session_scope
from .schemas import RunOut, RunStarted, ScraperOut

admin = APIRouter(prefix="/admin/v1", dependencies=[Depends(require_admin_key)])


def _execute_in_background(source_name: str, run_id: int, max_pages: int | None) -> None:
    """Drive the fetch+ingest for an already-created run on its own session."""
    adapter = REGISTRY[source_name]
    with session_scope() as session:
        run = session.get(IngestRun, run_id)
        if run is not None:
            execute_run(session, adapter, run, max_pages=max_pages)


def _start_run(
    db: DbSession, background: BackgroundTasks, adapter: SourceAdapter, max_pages: int | None
) -> RunStarted:
    run = create_run(db, adapter)
    background.add_task(_execute_in_background, adapter.name, run.id, max_pages)
    return RunStarted(run_id=run.id, source=adapter.name, status=run.status)


def _scraper_out(adapter: SourceAdapter, last_run: RunOut | None, now: datetime) -> ScraperOut:
    last_started = last_run.started_at if last_run is not None else None
    return ScraperOut(
        name=adapter.name,
        base_url=adapter.base_url,
        cadence=adapter.cadence.value,
        due=is_due(adapter.cadence, last_started, now),
        last_run=last_run,
    )


@admin.get("/scrapers", response_model=list[ScraperOut])
def list_scrapers(db: DbSession) -> list[ScraperOut]:
    now = utcnow()
    last = last_run_per_source(db)
    return [_scraper_out(adapter, last.get(name), now) for name, adapter in REGISTRY.items()]


@admin.get("/scrapers/{name}", response_model=ScraperOut)
def get_scraper(name: str, db: DbSession) -> ScraperOut:
    adapter = REGISTRY.get(name)
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown scraper {name!r}")
    last = last_run_per_source(db).get(name)
    return _scraper_out(adapter, last, utcnow())


@admin.post("/scrapers/{name}/run", status_code=status.HTTP_202_ACCEPTED, response_model=RunStarted)
def run_scraper(
    name: str,
    db: DbSession,
    background: BackgroundTasks,
    max_pages: Annotated[int | None, Query(ge=1)] = None,
) -> RunStarted:
    adapter = REGISTRY.get(name)
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown scraper {name!r}")
    if has_running(db, name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a run for {name!r} is already in progress")
    return _start_run(db, background, adapter, max_pages)


@admin.post("/scrapers/run-due", response_model=list[RunStarted])
def run_due(db: DbSession, background: BackgroundTasks) -> list[RunStarted]:
    """Trigger a background run for every scraper whose data is stale."""
    now = utcnow()
    last = last_run_per_source(db)
    started: list[RunStarted] = []
    for name, adapter in REGISTRY.items():
        last_run = last.get(name)
        last_started = last_run.started_at if last_run is not None else None
        if has_running(db, name) or not is_due(adapter.cadence, last_started, now):
            continue
        started.append(_start_run(db, background, adapter, None))
    return started


@admin.get("/runs", response_model=list[RunOut])
def get_runs(db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[RunOut]:
    return list_runs(db, limit)


@admin.get("/runs/{run_id}", response_model=RunOut)
def get_single_run(run_id: int, db: DbSession) -> RunOut:
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown run {run_id}")
    return run
