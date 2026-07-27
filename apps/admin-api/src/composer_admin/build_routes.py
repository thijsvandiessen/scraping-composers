"""Admin endpoints for the derived databases: promote gold, rebuild silver."""

import logging
from pathlib import Path

from composer_gold import (
    DEFAULT_GOLD_DB_PATH,
    DEFAULT_MIN_SITELINKS,
    PromoteConfig,
    promote,
    read_gold_manifest,
)
from composer_warehouse.build import read_build_manifest
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.rebuild import rebuild_silver, sqlite_db_path
from composer_warehouse.recordings import derive_recordings
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from . import routes
from .deps import dispose_db, require_admin_key, session_scope
from .schemas import GoldStatus, PromoteOptions, SilverStatus

log = logging.getLogger(__name__)

builds = APIRouter(prefix="/admin/v1", dependencies=[Depends(require_admin_key)])


def _promote_in_background(gold_path: str, config: PromoteConfig) -> None:
    """Rebuild the gold database; status lives in the gold manifest."""
    with session_scope() as session:
        try:
            # Concerts and recordings are silver-derived state the gold build
            # copies; refresh them first so the Promote button never publishes
            # stale derivations.
            derive_concerts(session)
            derive_recordings(session)
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


@builds.get("/gold", response_model=GoldStatus)
def gold_status() -> GoldStatus:
    """State of the gold database: last promote, its stats, current activity."""
    return _gold_status()


@builds.post("/promote", status_code=status.HTTP_202_ACCEPTED, response_model=GoldStatus)
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
    # Registry and bucket are read through the routes module so tests (and
    # future config) can swap them in one place.
    sources = [(adapter.name, adapter.base_url) for adapter in routes.REGISTRY.values()]
    try:
        rebuild_silver(routes._bucket(), sources)
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


@builds.get("/silver", response_model=SilverStatus)
def silver_status() -> SilverStatus:
    """State of the silver database: last rebuild, its stats, current activity."""
    return _silver_status()


@builds.post("/rebuild-silver", status_code=status.HTTP_202_ACCEPTED, response_model=SilverStatus)
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
