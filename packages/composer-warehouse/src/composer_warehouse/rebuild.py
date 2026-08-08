"""Rebuild the silver database from the bucket with the current heuristics.

The silver database mixes verbatim records with interpretation (entity
resolution, claims, work matching) that is baked in at first ingest — a new
record benefits from improved normalization or matching, an old one never
does. ``rebuild_silver`` closes that gap: it replays the latest complete
snapshot of every source from the bucket (the bronze tier — the only place
the full documents, claims included, live) into a fresh database, re-runs the
derivation passes, and atomically swaps the result in.

Human review decisions survive the rebuild. They are collected from the old
database first and re-applied after the replay: manual work matches
(``review --accept/--new``) are re-resolved by the work's
``(composer, title key)`` because work ids are random and change across
rebuilds; the target work is created if matching no longer produces it.

SQLite only: the atomic swap is a file replace, which has no Postgres
equivalent yet.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from composer_bronze.bucket import LOADABLE_STATUSES, Bucket
from composer_bronze.scraper import iter_from_bucket
from sqlalchemy import create_engine, make_url, select
from sqlalchemy.orm import Session

from .build import run_build
from .concerts import derive_concerts
from .db import init_db
from .ingestion import ingest_documents, new_work
from .models import Entity, RawWorkMention, Source, Work
from .recordings import derive_recordings
from .works import add_alias, extract_features

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebuildStats:
    sources_replayed: int = 0
    records_seen: int = 0
    records_new: int = 0
    work_decisions_applied: int = 0
    work_decisions_dropped: int = 0
    concerts: int = 0
    recordings: int = 0


@dataclass(frozen=True)
class WorkDecision:
    """A manual work match, keyed by the mention's identity and the work's
    ``(composer, title key)`` — work ids don't survive a rebuild."""

    source_name: str
    external_id: str
    composer_entity_id: uuid.UUID | None
    canonical_title: str
    title_key: str


def sqlite_db_path(database_url: str) -> Path:
    """The file behind a sqlite URL; rejects anything the swap can't handle."""
    url = make_url(database_url)
    if url.drivername.partition("+")[0] != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError(f"rebuild-silver requires a file-backed sqlite database URL, got {database_url!r}")
    return Path(url.database)


def collect_work_decisions(session: Session) -> list[WorkDecision]:
    """Snapshot the human review decisions to re-apply after a rebuild."""
    return [
        WorkDecision(
            source_name=source_name,
            external_id=external_id,
            composer_entity_id=work.composer_entity_id,
            canonical_title=work.canonical_title,
            title_key=work.title_key,
        )
        for source_name, external_id, work in session.execute(
            select(Source.name, RawWorkMention.external_id, Work)
            .join(RawWorkMention, RawWorkMention.source_id == Source.id)
            .join(Work, Work.id == RawWorkMention.work_id)
            .where(RawWorkMention.match_status == "manual_matched")
        ).tuples()
    ]


def _apply_work_decisions(session: Session, decisions: Sequence[WorkDecision]) -> tuple[int, int]:
    """Re-apply manual work matches, re-resolving (or re-creating) the work."""
    applied = dropped = 0
    for decision in decisions:
        mention = session.scalar(
            select(RawWorkMention)
            .join(Source, Source.id == RawWorkMention.source_id)
            .where(Source.name == decision.source_name, RawWorkMention.external_id == decision.external_id)
        )
        composer_exists = decision.composer_entity_id is None or (
            session.get(Entity, decision.composer_entity_id) is not None
        )
        if mention is None or not composer_exists:
            dropped += 1
            continue
        work = session.scalar(
            select(Work).where(
                Work.composer_entity_id == decision.composer_entity_id,
                Work.title_key == decision.title_key,
            )
        )
        if work is None:
            work = new_work(
                decision.composer_entity_id,
                decision.canonical_title,
                extract_features(decision.canonical_title),
            )
            session.add(work)
            session.flush()
        mention.work_id = work.id
        mention.match_status = "manual_matched"
        mention.match_method = "manual"
        add_alias(session, work.id, mention.raw_title, mention.source_id)
        applied += 1
    session.commit()
    return applied, dropped


def rebuild_silver(
    bucket: Bucket, sources: Sequence[tuple[str, str]], database_url: str | None = None
) -> RebuildStats:
    """Rebuild the silver database at ``database_url`` from the bucket.

    ``sources`` is the ``(name, base_url)`` of every source to replay (the
    scraper registry); a source without a complete snapshot is skipped. Builds
    into ``{path}.tmp`` and atomically swaps it in; progress and outcome land
    in ``{path}.manifest.json``. The old database is only replaced when every
    replay succeeds — a failure keeps it untouched.
    """
    from composer_config import settings

    url = database_url or settings.database_url
    db_path = sqlite_db_path(url)

    work_decisions: list[WorkDecision] = []
    if db_path.exists():
        old_engine = create_engine(f"sqlite:///{db_path}")
        with Session(old_engine) as old:
            work_decisions = collect_work_decisions(old)
        old_engine.dispose()

    def _build(tmp_path: Path) -> RebuildStats:
        tmp_path.unlink(missing_ok=True)
        engine = create_engine(f"sqlite:///{tmp_path}")
        factory = init_db(engine)
        with factory() as session:
            replayed = seen = new = 0
            for name, base_url in sources:
                snapshots = [s for s in bucket.list_snapshots(name) if s.manifest.status in LOADABLE_STATUSES]
                if not snapshots:
                    continue
                run_id = snapshots[-1].manifest.run_id
                log.info("replaying %s/%s", name, run_id)
                run = ingest_documents(session, name, base_url, iter_from_bucket(name, run_id, bucket))
                if run.status != "completed":
                    raise RuntimeError(f"replaying {name}/{run_id} failed: {run.error}")
                replayed += 1
                seen += run.records_seen
                new += run.records_new

            work_applied, work_dropped = _apply_work_decisions(session, work_decisions)
            concert_stats = derive_concerts(session)
            recording_stats = derive_recordings(session)
        engine.dispose()
        return RebuildStats(
            sources_replayed=replayed,
            records_seen=seen,
            records_new=new,
            work_decisions_applied=work_applied,
            work_decisions_dropped=work_dropped,
            concerts=concert_stats.concerts,
            recordings=recording_stats.recordings,
        )

    stats = run_build(db_path, _build)
    log.info("silver rebuilt at %s: %s", db_path, stats)
    return stats
