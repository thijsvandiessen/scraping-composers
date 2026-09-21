"""Admin endpoints for the derived databases: promote gold, rebuild silver."""

import logging
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

from composer_config import settings
from composer_gold import (
    DEFAULT_MIN_REFERRERS,
    DEFAULT_RULE1_CONFIG_PATH,
    EnsembleRule1Config,
    PersonRule1Config,
    PromoteConfig,
    Rule1Config,
    gold_target,
    promote,
)
from composer_warehouse.build import BuildTarget
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.rebuild import rebuild_silver, silver_target
from composer_warehouse.recordings import derive_recordings
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from . import snapshots
from .deps import dispose_db, require_admin_key, session_scope
from .schemas import BuildStatus, GoldStatus, PromoteOptions, Rule1ConfigBody, SilverStatus

log = logging.getLogger(__name__)

StatusT = TypeVar("StatusT", bound=BuildStatus)

builds = APIRouter(prefix="/admin/v1", dependencies=[Depends(require_admin_key)])


def _promote_in_background(gold_url: str, config: PromoteConfig) -> None:
    """Rebuild the gold database; status lives in the gold manifest."""
    with session_scope() as session:
        try:
            # Concerts and recordings are silver-derived state the gold build
            # copies; refresh them first so the Promote button never publishes
            # stale derivations.
            derive_concerts(session)
            derive_recordings(session)
            promote(session, gold_url, config)
        except Exception:
            # Recorded as a failed manifest by promote; log for the server console.
            log.exception("background promote failed")


def _current_rule1_config() -> Rule1Config:
    """Rule 1's thresholds, read fresh from disk on every call so a change made
    through ``PUT /admin/v1/rule1-config`` takes effect on the very next
    promote without a server restart."""
    return Rule1Config.from_json(Path(DEFAULT_RULE1_CONFIG_PATH))


def _rule1_config_body(config: Rule1Config) -> Rule1ConfigBody:
    return Rule1ConfigBody.model_validate(
        {"persons": asdict(config.persons), "ensembles": asdict(config.ensembles)}
    )


def _promote_config(options: PromoteOptions | None) -> tuple[str, PromoteConfig]:
    """Resolve the request body (or its absence) into a gold URL and config.

    ``min_referrers`` left out of the body falls back to the configured
    default. Rule 1's thresholds always come from the server's current
    ``rule1_config.json`` — the request body has no way to override them; use
    ``GET``/``PUT /admin/v1/rule1-config`` instead.
    """
    opts = options or PromoteOptions()
    gold_url = opts.gold_url or settings.gold_database_url
    min_referrers = opts.min_referrers if "min_referrers" in opts.model_fields_set else DEFAULT_MIN_REFERRERS
    config = PromoteConfig(
        rule1=_current_rule1_config(),
        min_referrers=min_referrers,
        drop_unevidenced_persons=opts.drop_unevidenced_persons,
        collapse_duplicates=opts.collapse_duplicates,
        prune_unreferenced=opts.prune_unreferenced,
    )
    return gold_url, config


def _target_status(target: BuildTarget | None, model: type[StatusT]) -> StatusT:
    """What a build target reports about itself, silver or gold alike."""
    if target is None:
        return model(
            backend="unsupported",
            exists=False,
            status=None,
            started_at=None,
            finished_at=None,
            error=None,
            stats={},
        )
    try:
        manifest = target.read_manifest()
        exists = target.exists()
    except SQLAlchemyError as exc:
        # Reading the manifest can fail on Postgres (the server is down, the
        # credentials are wrong); a file-backed manifest never could.
        return model(
            backend=target.backend(),
            exists=False,
            status=None,
            started_at=None,
            finished_at=None,
            error=str(exc),
            stats={},
        )
    return model(
        backend=target.backend(),
        exists=exists,
        status=manifest.status if manifest else None,
        started_at=manifest.started_at if manifest else None,
        finished_at=manifest.finished_at if manifest else None,
        error=manifest.error if manifest else None,
        stats=manifest.stats if manifest else {},
    )


def _gold_target(gold_url: str | None = None) -> BuildTarget | None:
    """The gold build target, or None when the URL supports no swap."""
    try:
        return gold_target(gold_url)
    except ValueError:
        return None


def _gold_status(gold_url: str | None = None) -> GoldStatus:
    return _target_status(_gold_target(gold_url), GoldStatus)


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
    gold_url, config = _promote_config(options)
    target = _gold_target(gold_url)
    if target is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "promote requires a sqlite file or a Postgres GOLD_DATABASE_URL",
        )
    manifest = target.read_manifest()
    if manifest is not None and manifest.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "a promote is already in progress")
    background.add_task(_promote_in_background, gold_url, config)
    current = _gold_status(gold_url)
    current.status = "running"
    return current


@builds.get("/rule1-config", response_model=Rule1ConfigBody)
def get_rule1_config() -> Rule1ConfigBody:
    """Rule 1's current concert/recording/composer/sitelink thresholds."""
    return _rule1_config_body(_current_rule1_config())


@builds.put("/rule1-config", response_model=Rule1ConfigBody)
def update_rule1_config(body: Rule1ConfigBody) -> Rule1ConfigBody:
    """Replace rule 1's thresholds wholesale; effective on the next promote."""
    config = Rule1Config(
        persons=PersonRule1Config(**body.persons.model_dump()),
        ensembles=EnsembleRule1Config(**body.ensembles.model_dump()),
    )
    config.write_json(Path(DEFAULT_RULE1_CONFIG_PATH))
    return body


def _silver_target() -> BuildTarget | None:
    """The silver build target, or None when DATABASE_URL supports no swap."""
    try:
        return silver_target(settings.database_url)
    except ValueError:
        return None


def _rebuild_silver_in_background() -> None:
    """Rebuild silver from the bucket; status lives in the silver manifest."""
    # Which sources get replayed is the bucket's own listing, not the scraper
    # registry — crawl-config sources have no adapter but do have extracted
    # documents in the bucket. The bucket and the base-URL lookup are reached
    # through the module that owns them so tests can swap them.
    try:
        rebuild_silver(snapshots.bucket(), snapshots.source_base_url)
    except Exception:
        # Recorded as a failed manifest by rebuild_silver; log for the console.
        log.exception("background silver rebuild failed")
    finally:
        # On SQLite the swap replaced the file, so pooled connections still
        # point at the old inode and must go. On Postgres the swap renames
        # schemas and name resolution happens per statement, so connections
        # follow it on their own — disposing is belt and braces there, and
        # still drops any whose transaction spanned the swap.
        dispose_db()


def _silver_status() -> SilverStatus:
    return _target_status(_silver_target(), SilverStatus)


@builds.get("/silver", response_model=SilverStatus)
def silver_status() -> SilverStatus:
    """State of the silver database: last rebuild, its stats, current activity."""
    return _silver_status()


@builds.post("/rebuild-silver", status_code=status.HTTP_202_ACCEPTED, response_model=SilverStatus)
def start_rebuild_silver(background: BackgroundTasks) -> SilverStatus:
    """Rebuild the silver database from the bucket (background)."""
    target = _silver_target()
    if target is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "rebuild-silver requires a sqlite file or a Postgres DATABASE_URL",
        )
    manifest = target.read_manifest()
    if manifest is not None and manifest.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "a silver rebuild is already in progress")
    background.add_task(_rebuild_silver_in_background)
    current = _silver_status()
    current.status = "running"
    return current
