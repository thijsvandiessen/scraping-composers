"""Rebuild the gold database from silver, applying the curation rules."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from composer_models import Base
from composer_warehouse.build import BuildManifest, SqliteFileTarget, read_build_manifest, run_build
from sqlalchemy import Engine
from sqlalchemy.orm import Session

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

    Every rule defaults to on; the two-argument ``promote(silver, gold_path)``
    call is the fully curated build. ``rule1`` (concert/recording/composer/
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


def read_gold_manifest(gold_path: str | Path) -> BuildManifest | None:
    return read_build_manifest(gold_path)


def promote(silver: Session, gold_path: str | Path, config: PromoteConfig | None = None) -> PromoteStats:
    """Rebuild the gold database at ``gold_path`` from the silver session.

    Builds into ``{gold_path}.tmp`` and atomically swaps it in, so readers
    never see a half-built database. Progress and outcome land in
    ``{gold_path}.manifest.json``.

    ``config`` tunes the run: the sitelink promotion signal and per-rule
    toggles (see ``PromoteConfig``). ``None`` runs the full curation with
    the sitelink signal off.
    """
    cfg = config or PromoteConfig()
    stats = run_build(SqliteFileTarget(Path(gold_path)), lambda engine: _build(silver, engine, cfg))
    log.info("gold promoted to %s: %s", gold_path, stats)
    return stats


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


def _build(silver: Session, gold_engine: Engine, config: PromoteConfig) -> PromoteStats:
    Base.metadata.create_all(gold_engine)

    build = GoldBuild(silver, config)
    build.select_persons()
    build.select_ensembles()
    with gold_engine.begin() as gold:
        copy_sources_and_runs(build, gold)
        copy_entities(build, gold, build.kept_roots)  # kept person representatives
        referenced = collect_person_claims(build)
        walk_referenced(build, referenced)
        collect_other_literal_claims(build)
        copy_entities(build, gold, build.kept_other)
        drop_pruned_object_claims(build)
        insert_claims(build, gold)
        copy_records(build, gold)
        copy_works_titles_mentions(build, gold)
        copy_concerts(build, gold)
        copy_recordings(build, gold)

    # The engine belongs to the build target, which disposes it as part of the
    # swap; closing it here would pull the file handle out from under it.
    return _stats(build)
