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

from composer_bronze.scraper import new_snapshot_id
from composer_crawler import (
    CRAWL_REGISTRY,
    CrawlConfig,
    CrawlConfigStore,
    Crawler,
    NextUrlFromJson,
    PageParam,
    Pagination,
    config_to_dict,
)
from composer_crawler.store import DEFAULT_CRAWL_CONFIGS_PATH
from composer_scrapers import REGISTRY
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status

from .deps import require_admin_key
from .routes import _bucket, _has_running_fetch, _last_snapshot, _snapshot_out
from .schemas import CrawlConfigIn, CrawlOut, FetchStarted

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
    pagination: Pagination | None = None
    if body.pagination is not None:
        if body.pagination.type == "page_param":
            pagination = PageParam(param=body.pagination.param, start=body.pagination.start)
        else:
            pagination = NextUrlFromJson(pointer=body.pagination.pointer)
    try:
        return CrawlConfig(
            name=name,
            seeds=tuple(body.seeds),
            follow_links=body.follow_links,
            allow_patterns=tuple(body.allow_patterns),
            max_depth=body.max_depth,
            max_pages=body.max_pages,
            pagination=pagination,
            request_delay_s=body.request_delay_s,
            respect_robots=body.respect_robots,
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


def _crawl_in_background(config: CrawlConfig, snapshot_id: str, max_pages: int | None) -> None:
    """Run a crawl to the bucket; status lives in the snapshot's manifest."""
    try:
        _crawler(config).crawl_to_bucket(_bucket(), max_pages=max_pages, run_id=snapshot_id)
    except Exception:
        # Recorded as a failed manifest by crawl_to_bucket; log for the console.
        log.exception("background crawl failed for %s/%s", config.name, snapshot_id)


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
