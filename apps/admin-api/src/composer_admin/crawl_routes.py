"""Crawl-config endpoints: manage stored ``CrawlConfig``s and start crawls.

Stored configs live in the crawl-configs JSON file (see
``composer_crawler.store``); code-registered ones from ``CRAWL_REGISTRY`` are
listed alongside them but are read-only here — they change through the source
tree, not the dashboard. Crawl runs reuse the bucket-snapshot machinery, so
status reporting and the running-crawl guard work exactly like scraper fetches.
"""

import logging
from pathlib import Path
from typing import Annotated, Any

from composer_bronze.bucket import latest_document_run_id, latest_loadable_run_id
from composer_bronze.scraper import new_snapshot_id, write_documents
from composer_crawler import (
    CRAWL_REGISTRY,
    CrawlConfig,
    CrawlConfigStore,
    Crawler,
    config_to_dict,
)
from composer_crawler.records import iter_crawl_records
from composer_crawler.store import DEFAULT_CRAWL_CONFIGS_PATH
from composer_extract import OllamaExtractor, extract_documents, extract_recording_documents
from composer_scrapers import REGISTRY
from composer_warehouse.ingestion import create_run
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status

from .crud import has_running
from .deps import DbSession, require_admin_key
from .pipeline import run_pipeline
from .routes import _bucket, _has_running_fetch, _last_snapshot, _process_in_background, _snapshot_out
from .schemas import CrawlConfigIn, CrawlOut, FetchStarted, RunStarted

log = logging.getLogger(__name__)

crawls = APIRouter(prefix="/admin/v1", dependencies=[Depends(require_admin_key)])


def _store() -> CrawlConfigStore:
    return CrawlConfigStore(Path(DEFAULT_CRAWL_CONFIGS_PATH))


def _merged() -> dict[str, CrawlConfig]:
    """Stored and code-registered configs; the code registry wins on collision."""
    return {**_store().load(), **CRAWL_REGISTRY}


def _crawl_out(config: CrawlConfig) -> CrawlOut:
    data: dict[str, Any] = config_to_dict(config)
    data["editable"] = config.name not in CRAWL_REGISTRY
    last = _last_snapshot(_bucket(), config.name)
    data["last_snapshot"] = _snapshot_out(last) if last is not None else None
    return CrawlOut.model_validate(data)


def _to_config(name: str, body: CrawlConfigIn) -> CrawlConfig:
    try:
        return CrawlConfig(
            name=name,
            seeds=tuple(body.seeds),
            use_sitemap=body.use_sitemap,
            use_common_crawl=body.use_common_crawl,
            allow_patterns=tuple(body.allow_patterns),
            relevance_query=body.relevance_query,
            score_threshold=body.score_threshold,
            follow_links=body.follow_links,
            max_depth=body.max_depth,
            max_pages=body.max_pages,
            excluded_selector=body.excluded_selector,
            request_delay_s=body.request_delay_s,
            respect_robots=body.respect_robots,
            extract_kind=body.extract_kind,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _reject_code_registered(name: str) -> None:
    if name in CRAWL_REGISTRY:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"crawl {name!r} is code-registered; edit it in the source tree"
        )


def _crawler(config: CrawlConfig) -> Crawler:
    """Build the crawler for a config; a seam for tests to inject a client."""
    return Crawler(config)


def _crawl_in_background(config: CrawlConfig, snapshot_id: str, max_pages: int | None) -> bool:
    """Run a crawl to the bucket; status lives in the snapshot's manifest.

    Returns whether the stage succeeded, so the pipeline knows not to extract
    from a snapshot that was never finished.
    """
    try:
        _crawler(config).crawl_to_bucket(_bucket(), max_pages=max_pages, run_id=snapshot_id)
    except Exception:
        # Recorded as a failed manifest by crawl_to_bucket; log for the console.
        log.exception("background crawl failed for %s/%s", config.name, snapshot_id)
        return False
    return True


def _extractor() -> OllamaExtractor:
    """The LLM extractor; a seam for tests to inject a fake model."""
    return OllamaExtractor.from_settings()


def _extract_in_background(name: str, crawl_run_id: str, snapshot_id: str, extract_kind: str) -> bool:
    """Run the model over a crawl snapshot, writing documents to a new snapshot.

    Pages whose output the model mangles are skipped by ``extract_documents``
    itself; only a wholesale failure (Ollama unreachable, or nothing usable at
    all) gets here. Returns whether the stage succeeded.
    """
    bucket = _bucket()
    try:
        records = iter_crawl_records(name, crawl_run_id, bucket)
        extract = extract_recording_documents if extract_kind == "recordings" else extract_documents
        docs = extract(records, source_name=name, extractor=_extractor())
        write_documents(bucket, name, docs, run_id=snapshot_id)
    except Exception:
        # Recorded as a failed manifest by write_documents; log for the console.
        log.exception("background extract failed for %s/%s", name, snapshot_id)
        return False
    return True


def _pipeline_in_background(config: CrawlConfig, crawl_id: str) -> None:
    """Drive the whole chain, handing it the stages this module owns so the test
    seams above still apply to a pipeline run."""
    run_pipeline(config, crawl_id, _crawl_in_background, _extract_in_background)


@crawls.get("/crawls", response_model=list[CrawlOut])
def list_crawls() -> list[CrawlOut]:
    """Every crawl config — stored and code-registered — with its last run."""
    return [_crawl_out(config) for _, config in sorted(_merged().items())]


@crawls.get("/crawls/{name}", response_model=CrawlOut)
def get_crawl(name: str) -> CrawlOut:
    config = _merged().get(name)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown crawl {name!r}")
    return _crawl_out(config)


@crawls.put("/crawls/{name}", response_model=CrawlOut)
def put_crawl(name: str, body: CrawlConfigIn) -> CrawlOut:
    """Create or update a stored crawl config."""
    _reject_code_registered(name)
    if name in REGISTRY:
        raise HTTPException(status.HTTP_409_CONFLICT, f"crawl {name!r} collides with a scraper source")
    config = _to_config(name, body)
    _store().save(config)
    return _crawl_out(config)


@crawls.delete("/crawls/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_crawl(name: str) -> Response:
    """Remove a stored crawl config; a crawl already running finishes normally."""
    _reject_code_registered(name)
    if not _store().delete(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown crawl {name!r}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@crawls.post("/crawls/{name}/fetch", status_code=status.HTTP_202_ACCEPTED, response_model=FetchStarted)
def fetch_crawl(
    name: str,
    background: BackgroundTasks,
    max_pages: Annotated[int | None, Query(ge=1)] = None,
) -> FetchStarted:
    """Start a background crawl for a config, into a new bucket snapshot."""
    config = _merged().get(name)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown crawl {name!r}")
    if _has_running_fetch(_bucket(), name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a crawl for {name!r} is already in progress")
    snapshot_id = new_snapshot_id()
    background.add_task(_crawl_in_background, config, snapshot_id, max_pages)
    return FetchStarted(source=name, snapshot_id=snapshot_id, status="running")


@crawls.post("/crawls/{name}/extract", status_code=status.HTTP_202_ACCEPTED, response_model=FetchStarted)
def extract_crawl(name: str, background: BackgroundTasks) -> FetchStarted:
    """Start a background LLM extraction over the crawl's latest snapshot.

    Reads the pages a crawl already stored and writes work-mention/entity
    documents to a new snapshot under the same source name, which ``process``
    then ingests like any other.
    """
    config = _merged().get(name)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown crawl {name!r}")
    bucket = _bucket()
    if _has_running_fetch(bucket, name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a run for {name!r} is already in progress")
    crawl_run_id = latest_loadable_run_id(bucket, name)
    if crawl_run_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"crawl {name!r} has no completed snapshot to extract")
    snapshot_id = new_snapshot_id()
    background.add_task(_extract_in_background, name, crawl_run_id, snapshot_id, config.extract_kind)
    return FetchStarted(source=name, snapshot_id=snapshot_id, status="running")


@crawls.post("/crawls/{name}/run", status_code=status.HTTP_202_ACCEPTED, response_model=FetchStarted)
def run_crawl_pipeline(name: str, db: DbSession, background: BackgroundTasks) -> FetchStarted:
    """Crawl, extract and load in one go — the unattended path.

    Nothing about the three stages changes; they simply run back to back, each
    still recording its own snapshot or run, and the chain stops at the first
    failure. Returns the crawl snapshot, which is what starts immediately.
    """
    config = _merged().get(name)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown crawl {name!r}")
    if _has_running_fetch(_bucket(), name) or has_running(db, name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a run for {name!r} is already in progress")
    snapshot_id = new_snapshot_id()
    background.add_task(_pipeline_in_background, config, snapshot_id)
    return FetchStarted(source=name, snapshot_id=snapshot_id, status="running")


@crawls.post("/crawls/{name}/process", status_code=status.HTTP_202_ACCEPTED, response_model=RunStarted)
def process_crawl(name: str, db: DbSession, background: BackgroundTasks) -> RunStarted:
    """Load the crawl's latest LLM-extracted ``documents`` snapshot into the DB.

    The per-crawl counterpart to ``/snapshots/{source}/{id}/process``: it
    resolves the newest extracted snapshot server-side (skipping raw-page
    crawls), so the dashboard can drive Crawl → Extract → Load from one row.
    """
    config = _merged().get(name)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown crawl {name!r}")
    if has_running(db, name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"a run for {name!r} is already in progress")
    run_id = latest_document_run_id(_bucket(), name)
    if run_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"crawl {name!r} has no extracted snapshot; run extract first"
        )
    base_url = config.seeds[0] if config.seeds else ""
    run = create_run(db, name, base_url)
    background.add_task(_process_in_background, name, run_id, run.id)
    return RunStarted(run_id=run.id, source=name, status=run.status)
