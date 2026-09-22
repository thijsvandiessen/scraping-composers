"""Rebuild the gold database from silver, applying the curation rules."""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field

from composer_config import settings
from composer_models import Base
from composer_models.alembic_support import stamp_head
from composer_models.db import get_engine, resync_pk_sequence
from composer_warehouse.build import BuildManifest, BuildTarget, build_target, run_build
from composer_warehouse.postgres import schema_read_lock
from sqlalchemy import Connection, Engine, Integer, create_engine, make_url, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from ._claims import (
    collect_other_literal_claims,
    collect_person_claims,
    drop_pruned_object_claims,
    insert_claims,
    walk_referenced,
)
from ._copy import (
    copy_concerts,
    copy_entities,
    copy_recordings,
    copy_records,
    copy_sources_and_runs,
    copy_works_titles_mentions,
)
from ._rule1_config import Rule1Config
from ._selection import GoldBuild

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromoteStats:
    persons_kept: int = 0
    persons_dropped: int = 0
    persons_kept_by_appearances: int = 0
    persons_promoted_by_sitelinks: int = 0
    ensembles_kept: int = 0
    ensembles_dropped: int = 0
    duplicates_collapsed: int = 0
    entities_kept_other: int = 0
    entities_pruned: int = 0
    claims: int = 0
    records: int = 0
    works: int = 0
    work_titles: int = 0
    mentions: int = 0
    concerts: int = 0
    concert_participant_links: int = 0
    unresolved_participant_names: int = 0
    recordings: int = 0
    recording_participant_links: int = 0
    unresolved_recording_participant_names: int = 0


@dataclass(frozen=True)
class PromoteConfig:
    """Per-run knobs of the promotion: the curation rules and their signals.

    Every rule defaults to on; ``promote(silver)`` with no config is the fully
    curated build. ``rule1`` (concert/recording/composer/
    sitelink thresholds, see ``Rule1Config``) only matters while rule 1 is on —
    with rule 1 off every person and ensemble is kept anyway. ``min_referrers``
    only matters while rule 3 is on — with rule 3 off every entity is kept; at
    its default of 1 it reproduces the historical "keep anything referenced"
    behaviour.
    """

    rule1: Rule1Config = field(default_factory=Rule1Config)
    min_referrers: int = 1  # rule 3 threshold: keep entities with >= N distinct referrers
    drop_unevidenced_persons: bool = True  # rule 1
    collapse_duplicates: bool = True  # rule 2
    prune_unreferenced: bool = True  # rule 3


# The gold manifest predates the shared build helper; keep the old names
# working for existing callers.
GoldManifest = BuildManifest


def gold_target(gold_url: str | None = None) -> BuildTarget:
    """The swap target for gold at ``gold_url`` (default ``GOLD_DATABASE_URL``).

    A SQLite file is swapped by file replace, with its manifest beside it in
    ``{file}.manifest.json``; Postgres by renaming ``GOLD_SCHEMA``, with its
    manifest in ``composer_meta.build_manifest``.
    """
    return build_target(gold_url or settings.gold_database_url, settings.gold_schema)


def gold_engine(gold_url: str | None = None) -> Engine:
    """An engine for reading gold that follows every swap without a restart.

    SQLite: NullPool, because the swap replaces the file and a pooled
    connection would keep serving the old inode. Postgres: pinned to
    ``GOLD_SCHEMA`` by name, and names resolve per statement, so pooled
    connections see the renamed-in schema on their own.
    """
    url = gold_url or settings.gold_database_url
    if make_url(url).get_backend_name() == "sqlite":
        return create_engine(url, poolclass=NullPool)
    return get_engine(url, schema=settings.gold_schema)


def read_gold_manifest(gold_url: str | None = None) -> BuildManifest | None:
    return gold_target(gold_url).read_manifest()


def promote(
    silver: Session, gold_url: str | None = None, config: PromoteConfig | None = None
) -> PromoteStats:
    """Rebuild gold at ``gold_url`` (default ``GOLD_DATABASE_URL``) from silver.

    Builds into a staging area and atomically swaps it in, so readers never
    see a half-built database (see :func:`gold_target`). Progress and outcome
    land in the target's manifest.

    ``config`` tunes the run: the sitelink promotion signal and per-rule
    toggles (see ``PromoteConfig``). ``None`` runs the full curation with
    the sitelink signal off.
    """
    cfg = config or PromoteConfig()
    target = gold_target(gold_url)
    with _silver_read_lock(silver):
        stats = run_build(target, lambda engine: _build(silver, engine, cfg))
    log.info("gold promoted to %s: %s", target.describe(), stats)
    return stats


def _silver_read_lock(silver: Session) -> AbstractContextManager[None]:
    """Keep silver from being swapped out while promote reads it.

    Only Postgres needs this. On SQLite the open file keeps the old inode
    alive across a rebuild's file replace, so promote finishes on the silver it
    started with.
    """
    url = silver.get_bind().engine.url
    if url.get_backend_name() != "postgresql":
        return nullcontext()
    return schema_read_lock(url, settings.silver_schema)


def _stats(build: GoldBuild) -> PromoteStats:
    return PromoteStats(
        persons_kept=len(build.kept_roots),
        persons_dropped=len(build.all_persons) - len(build.kept_members),
        persons_kept_by_appearances=len(build.appearance_roots),
        persons_promoted_by_sitelinks=len(build.sitelink_roots - build.evidence_roots),
        ensembles_kept=len(build.kept_ensembles),
        ensembles_dropped=len(build.unevidenced_ensembles),
        duplicates_collapsed=len(build.kept_members) - len(build.kept_roots),
        entities_kept_other=len(build.kept_other),
        entities_pruned=len(build.all_other - build.kept_other),
        claims=len(build.claim_rows),
        records=build.record_count,
        works=build.work_count,
        work_titles=build.title_count,
        mentions=build.mention_count,
        concerts=build.concert_count,
        concert_participant_links=build.participant_links,
        unresolved_participant_names=len(build.unresolved_names),
        recordings=build.recording_count,
        recording_participant_links=build.recording_participant_links,
        unresolved_recording_participant_names=len(build.recording_unresolved_names),
    )


def _build(silver: Session, engine: Engine, config: PromoteConfig) -> PromoteStats:
    Base.metadata.create_all(engine)

    build = GoldBuild(silver, config)
    build.select_persons()
    build.select_ensembles()
    with engine.begin() as gold:
        copy_sources_and_runs(build, gold)
        copy_entities(build, gold, build.kept_roots)  # kept person representatives
        referenced = collect_person_claims(build)
        walk_referenced(build, referenced)
        collect_other_literal_claims(build)
        copy_entities(build, gold, build.kept_other)
        drop_pruned_object_claims(build)
        # Records before claims: a claim points at the record asserting it.
        # SQLite never checked that foreign key; Postgres does.
        copy_records(build, gold)
        insert_claims(build, gold)
        copy_works_titles_mentions(build, gold)
        copy_concerts(build, gold)
        copy_recordings(build, gold)
        _resync_sequences(gold)

    if engine.dialect.name == "postgresql":
        # Stamped for the same reason rebuild-silver stamps: so the swapped-in
        # schema reads as migrated rather than hand-made.
        stamp_head(engine)
        _analyze(engine)

    # The engine belongs to the build target, which disposes it as part of the
    # swap; closing it here would pull the file handle out from under it.
    return _stats(build)


def _analyze(engine: Engine) -> None:
    """Give the planner statistics before the swap, not whenever autovacuum
    gets round to it: the first requests against a fresh gold otherwise plan
    blind. Table by table, because a bare ANALYZE covers every schema."""
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            conn.execute(text(f'ANALYZE "{table.name}"'))


def _resync_sequences(gold: Connection) -> None:
    """Move every serial id sequence past the ids copied over from silver.

    Gold keeps silver's integer ids, which bypasses the sequences; on Postgres
    they would stay at 1 and the first insert into a promoted gold would
    collide. A no-op on SQLite.
    """
    for table in Base.metadata.sorted_tables:
        pk = list(table.primary_key.columns)
        if len(pk) == 1 and isinstance(pk[0].type, Integer):
            resync_pk_sequence(gold, table.name, pk[0].name)
