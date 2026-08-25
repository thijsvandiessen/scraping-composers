"""Rebuild the silver database from the bucket with the current heuristics.

The silver database mixes verbatim records with interpretation (entity
resolution, claims, work matching) that is baked in at first ingest — a new
record benefits from improved normalization or matching, an old one never
does. ``rebuild_silver`` closes that gap: it replays every document snapshot
of every source in the bucket (the bronze tier — the only place the full
documents, claims included, live) into a fresh database, re-runs the
derivation passes, and atomically swaps the result in.

What gets replayed comes from the bucket itself, not from
``composer_scrapers.REGISTRY``: a crawl-config source has no adapter, but the
``extract`` step writes its LLM-derived documents into the bucket in exactly
the format a scraper writes, and those documents are the only source of
recordings. Driving the replay off the registry silently dropped every one of
them (#182).

Human review decisions survive the rebuild. They are collected from the old
database first and re-applied after the replay:

- person matches (``person-review --accept/--reject``) carry over directly —
  entity ids are deterministic (uuid5 of kind + dedup key), so the same
  person gets the same id in the new database;
- manual work matches (``review --accept/--new``) are re-resolved by the
  work's ``(composer, title key)`` because work ids are random and change
  across rebuilds; the target work is created if matching no longer produces
  it.

The swap is atomic on both backends: a file replace on SQLite, a schema
rename on Postgres (see :mod:`composer_warehouse.postgres`).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from composer_bronze.bucket import Bucket, all_document_run_ids
from composer_bronze.scraper import iter_all_from_bucket
from composer_models import Base, Entity, PersonMatch, RawWorkMention, Source, Work
from composer_models.alembic_support import stamp_head
from composer_models.db import get_engine, init_db
from sqlalchemy import Engine, inspect, make_url, select
from sqlalchemy.orm import Session

from .build import BuildTarget, SqliteFileTarget, run_build
from .concerts import derive_concerts
from .ingestion import ingest_documents, new_work
from .persons import dedupe_persons
from .recordings import derive_recordings
from .works import add_alias, extract_features

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebuildStats:
    sources_replayed: int = 0
    records_seen: int = 0
    records_new: int = 0
    persons_auto_linked: int = 0
    person_decisions_applied: int = 0
    person_decisions_dropped: int = 0
    work_decisions_applied: int = 0
    work_decisions_dropped: int = 0
    concerts: int = 0
    recordings: int = 0


@dataclass(frozen=True)
class PersonDecision:
    """A reviewed person-duplicate pair, portable across rebuilds by its
    deterministic entity ids."""

    entity_id: uuid.UUID
    canonical_entity_id: uuid.UUID
    status: str  # accepted | rejected
    score: float
    method: str | None


@dataclass(frozen=True)
class WorkDecision:
    """A manual work match, keyed by the mention's identity and the work's
    ``(composer, title key)`` — work ids don't survive a rebuild."""

    source_name: str
    external_id: str
    composer_entity_id: uuid.UUID | None
    canonical_title: str
    title_key: str


def silver_target(database_url: str | None = None) -> BuildTarget:
    """The swap target for the silver database at ``database_url``.

    A SQLite file gets an atomic file replace; Postgres gets an atomic schema
    rename. Raises ``ValueError`` for a URL neither can handle — an in-memory
    SQLite database has no file to swap, and no other dialect is supported.
    """
    from composer_config import settings

    url = make_url(database_url or settings.database_url)
    backend = url.get_backend_name()
    if backend == "postgresql":
        from .postgres import PostgresSchemaTarget

        return PostgresSchemaTarget(url, settings.silver_schema)
    if backend == "sqlite" and url.database and url.database != ":memory:":
        return SqliteFileTarget(Path(url.database))
    raise ValueError(f"rebuild-silver needs a sqlite file or a Postgres URL, got {database_url!r}")


def collect_decisions(session: Session) -> tuple[list[PersonDecision], list[WorkDecision]]:
    """Snapshot the human review decisions to re-apply after a rebuild."""
    person_decisions = [
        PersonDecision(
            entity_id=m.entity_id,
            canonical_entity_id=m.canonical_entity_id,
            status=m.status,
            score=m.score,
            method=m.method,
        )
        for m in session.scalars(select(PersonMatch).where(PersonMatch.status.in_(["accepted", "rejected"])))
    ]
    work_decisions = [
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
    return person_decisions, work_decisions


def _apply_person_decisions(session: Session, decisions: Sequence[PersonDecision]) -> tuple[int, int]:
    """Re-insert reviewed person pairs; accepted ones re-link the duplicate.

    Runs before ``dedupe_persons`` so the carried rows land in its decided set:
    accepted pairs stay linked, rejected pairs are not re-proposed. Decisions
    whose entities no longer exist (the source stopped reporting them) drop.

    The link written here is provisional — ``dedupe_persons`` rebuilds every
    person link from the clusters these rows imply, so an accepted pair ends up
    pointing at its cluster's canonical rather than at the entity the reviewer
    happened to see. It is written anyway so the carry-over stands on its own
    when the dedupe pass is skipped.
    """
    applied = dropped = 0
    for decision in decisions:
        duplicate = session.get(Entity, decision.entity_id)
        canonical = session.get(Entity, decision.canonical_entity_id)
        if duplicate is None or canonical is None:
            dropped += 1
            continue
        session.add(
            PersonMatch(
                entity_id=decision.entity_id,
                canonical_entity_id=decision.canonical_entity_id,
                score=decision.score,
                method=decision.method,
                status=decision.status,
            )
        )
        if decision.status == "accepted":
            duplicate.canonical_entity_id = decision.canonical_entity_id
        applied += 1
    session.commit()
    return applied, dropped


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


def replayable_sources(bucket: Bucket) -> list[tuple[str, list[str]]]:
    """Every bucket source worth replaying, with the run ids to replay for it.

    The source list is the bucket's own (``list_sources``), so crawl-config
    sources — which have no adapter to look up — are replayed like any other.
    Each gets *all* of its loadable ``documents`` runs rather than the latest
    one, matching what ``process`` and the admin API already do: ingestion is
    idempotent per external_id, so the union is every unique record ever seen.
    The ``kind`` filter is what makes that safe — a crawl source's newest
    snapshot is often raw ``pages``, which ``deserialize_document`` refuses.

    Sources whose only snapshots are raw pages (crawled but not yet extracted)
    have nothing loadable and are left out.
    """
    with_runs = ((name, all_document_run_ids(bucket, name)) for name in bucket.list_sources())
    return [(name, run_ids) for name, run_ids in with_runs if run_ids]


def _replay(
    engine: Engine,
    bucket: Bucket,
    base_url_for: Callable[[str], str],
    person_decisions: Sequence[PersonDecision],
    work_decisions: Sequence[WorkDecision],
) -> RebuildStats:
    """Build a complete silver database on ``engine`` from the bucket."""
    if engine.dialect.name != "sqlite":
        # init_db only creates tables on SQLite. Build the staging schema from
        # the models and stamp it at head, so what the swap promotes looks
        # migrated rather than hand-made — the drift test guarantees the two
        # are the same schema.
        Base.metadata.create_all(engine)
        stamp_head(engine)
    factory = init_db(engine)
    with factory() as session:
        replayed = seen = new = 0
        for name, run_ids in replayable_sources(bucket):
            log.info("replaying %s: %d run(s) %s", name, len(run_ids), ", ".join(run_ids))
            records = iter_all_from_bucket(name, run_ids, bucket)
            run = ingest_documents(session, name, base_url_for(name), records)
            if run.status != "completed":
                raise RuntimeError(f"replaying {name} ({', '.join(run_ids)}) failed: {run.error}")
            replayed += 1
            seen += run.records_seen
            new += run.records_new

        person_applied, person_dropped = _apply_person_decisions(session, person_decisions)
        auto_linked, _needs_review = dedupe_persons(session)
        work_applied, work_dropped = _apply_work_decisions(session, work_decisions)
        concert_stats = derive_concerts(session)
        recording_stats = derive_recordings(session)

    return RebuildStats(
        sources_replayed=replayed,
        records_seen=seen,
        records_new=new,
        persons_auto_linked=auto_linked,
        person_decisions_applied=person_applied,
        person_decisions_dropped=person_dropped,
        work_decisions_applied=work_applied,
        work_decisions_dropped=work_dropped,
        concerts=concert_stats.concerts,
        recordings=recording_stats.recordings,
    )


def rebuild_silver(
    bucket: Bucket,
    base_url_for: Callable[[str], str] | None = None,
    database_url: str | None = None,
) -> RebuildStats:
    """Rebuild the silver database at ``database_url`` from the bucket.

    Which sources are replayed is the bucket's business (see
    :func:`replayable_sources`). ``base_url_for`` only supplies the ``Source``
    row's ``base_url`` for a source name — the scraper registry and the crawl
    configs both live in packages this one does not depend on, so the caller
    resolves it; the default leaves it empty, as it already is for the bucket
    sources that have neither.

    Builds into a staging area and atomically swaps it in; progress and outcome
    land in the target's manifest. The old database is only replaced when every
    replay succeeds — a failure keeps it untouched.
    """
    from composer_config import settings

    url = database_url or settings.database_url
    target = silver_target(url)

    person_decisions: list[PersonDecision] = []
    work_decisions: list[WorkDecision] = []
    # Ask the target first: connecting to a sqlite URL creates the file, so a
    # first-ever rebuild would leave an empty database beside the real one.
    if target.exists():
        old_engine = get_engine(url)
        try:
            if inspect(old_engine).has_table(PersonMatch.__tablename__):
                with Session(old_engine) as old:
                    person_decisions, work_decisions = collect_decisions(old)
        finally:
            old_engine.dispose()

    resolve = base_url_for if base_url_for is not None else lambda _name: ""
    stats = run_build(
        target, lambda engine: _replay(engine, bucket, resolve, person_decisions, work_decisions)
    )
    log.info("silver rebuilt at %s: %s", target.describe(), stats)
    return stats
