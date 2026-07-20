# pylint: disable=too-many-lines
"""Rebuild the gold database from silver, applying the curation rules."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from composer_warehouse.build import BuildManifest, read_build_manifest, run_build
from composer_warehouse.models import (
    Base,
    Claim,
    Concert,
    ConcertParticipant,
    ConcertWork,
    Entity,
    EntityRecord,
    IngestRun,
    RawWorkMention,
    Source,
    Work,
    WorkTitle,
)
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

INSERT_BATCH = 1000
# SQLite limits the number of bound variables; chunk large IN () lists.
IN_CHUNK = 500


@dataclass(frozen=True)
class PromoteStats:
    persons_kept: int = 0
    persons_dropped: int = 0
    persons_promoted_by_sitelinks: int = 0
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


@dataclass(frozen=True)
class PromoteConfig:
    """Per-run knobs of the promotion: the curation rules and their signals.

    Every rule defaults to on; the two-argument ``promote(silver, gold_path)``
    call is the fully curated build. ``min_sitelinks`` only matters while
    rule 1 is on — with rule 1 off every person is kept anyway.
    """

    min_sitelinks: int | None = None
    drop_unevidenced_persons: bool = True  # rule 1
    collapse_duplicates: bool = True  # rule 2
    prune_unreferenced: bool = True  # rule 3


# The gold manifest predates the shared build helper; keep the old names
# working for existing callers.
GoldManifest = BuildManifest


def read_gold_manifest(gold_path: str | Path) -> BuildManifest | None:
    return read_build_manifest(gold_path)


def _chunked(ids: list[Any]) -> Iterable[list[Any]]:
    for i in range(0, len(ids), IN_CHUNK):
        yield ids[i : i + IN_CHUNK]


def _resolve_roots(silver: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Map every canonical-linked person to its transitive canonical root."""
    links: dict[uuid.UUID, uuid.UUID] = {
        entity_id: canonical_id
        for entity_id, canonical_id in silver.execute(
            select(Entity.id, Entity.canonical_entity_id).where(Entity.canonical_entity_id.is_not(None))
        ).tuples()
        if canonical_id is not None  # guaranteed by the WHERE; narrows the type
    }
    roots: dict[uuid.UUID, uuid.UUID] = {}
    for start in links:
        node = start
        seen = {node}
        while node in links and links[node] not in seen:
            node = links[node]
            seen.add(node)
        roots[start] = node
    return roots


def _sitelink_roots(
    silver: Session,
    root: Callable[[uuid.UUID], uuid.UUID],
    all_persons: set[uuid.UUID],
    min_sitelinks: int | None,
) -> set[uuid.UUID]:
    """Person roots whose Wikipedia sitelink count reaches ``min_sitelinks``.

    Sitelink counts are stored as string literals on the ``sitelink_count``
    claim; the count is taken per dedup cluster (max across its members, so the
    best-documented spelling wins) and non-numeric values are ignored. Returns
    an empty set when no threshold is configured.
    """
    if min_sitelinks is None:
        return set()
    all_person_roots = {root(p) for p in all_persons}
    max_sitelinks: dict[uuid.UUID, int] = {}
    for subject_id, value in silver.execute(
        select(Claim.subject_id, Claim.value).where(Claim.predicate == "sitelink_count")
    ).tuples():
        if value is None:
            continue
        try:
            count = int(value)
        except ValueError:
            continue
        r = root(subject_id)
        if count > max_sitelinks.get(r, -1):
            max_sitelinks[r] = count
    return {r for r, count in max_sitelinks.items() if r in all_person_roots and count >= min_sitelinks}


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
    stats = run_build(gold_path, lambda tmp: _build(silver, tmp, cfg))
    log.info("gold promoted to %s: %s", gold_path, stats)
    return stats


def _build(silver: Session, tmp_path: Path, config: PromoteConfig) -> PromoteStats:
    tmp_path.unlink(missing_ok=True)
    gold_engine = create_engine(f"sqlite:///{tmp_path}")
    Base.metadata.create_all(gold_engine)

    # --- rule 2 groundwork: duplicate clusters -----------------------------
    # With the rule off, no links are resolved and every spelling stands on
    # its own (including for rule 1's evidence check).
    roots = _resolve_roots(silver) if config.collapse_duplicates else {}

    def root(entity_id: uuid.UUID) -> uuid.UUID:
        return roots.get(entity_id, entity_id)

    all_persons = set(silver.scalars(select(Entity.id).where(Entity.kind == "person")))

    if config.drop_unevidenced_persons:
        # --- rule 1: persons with performance/work evidence ----------------
        mention_composers = set(
            silver.scalars(
                select(RawWorkMention.composer_entity_id)
                .where(RawWorkMention.composer_entity_id.is_not(None))
                .distinct()
            )
        )
        perf_sources = select(RawWorkMention.source_id).distinct().scalar_subquery()
        archive_reported = set(
            silver.scalars(
                select(EntityRecord.entity_id)
                .where(EntityRecord.source_id.in_(perf_sources), EntityRecord.entity_id.is_not(None))
                .distinct()
            )
        )
        evidence = mention_composers | archive_reported
        evidence_roots = {root(p) for p in all_persons if p in evidence}

        # --- extra signal: culturally significant persons by sitelink count -
        # Wikipedia sitelink count (from Wikidata) is a proxy for significance.
        # When a threshold is set, a person clearing it is promoted even without
        # the performance/work evidence above; this only ever adds persons,
        # never drops.
        sitelink_roots = _sitelink_roots(silver, root, all_persons, config.min_sitelinks)

        kept_roots = evidence_roots | sitelink_roots
    else:
        evidence_roots = set()
        sitelink_roots = set()
        kept_roots = {root(p) for p in all_persons}

    kept_members = {p for p in all_persons if root(p) in kept_roots}

    with gold_engine.begin() as gold:
        # --- FK targets: sources and runs, wholesale -----------------------
        for row in silver.execute(select(Source)).scalars():
            gold.execute(
                insert(Source).values(
                    id=row.id, name=row.name, base_url=row.base_url, created_at=row.created_at
                )
            )
        run_rows = [
            {
                "id": r.id,
                "source_id": r.source_id,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "status": r.status,
                "records_seen": r.records_seen,
                "records_new": r.records_new,
                "error": r.error,
            }
            for r in silver.execute(select(IngestRun)).scalars()
        ]
        if run_rows:
            gold.execute(insert(IngestRun), run_rows)

        # --- kept person representatives (canonical link resolved) ---------
        def entity_row(e: Entity) -> dict[str, Any]:
            return {
                "id": e.id,
                "kind": e.kind,
                "dedup_key": e.dedup_key,
                "label": e.label,
                "canonical_entity_id": None,
                "created_at": e.created_at,
                "first_ingested_at": e.first_ingested_at,
                "last_ingested_at": e.last_ingested_at,
                "last_edited_at": e.last_edited_at,
            }

        for chunk in _chunked(sorted(kept_roots, key=str)):
            rows = [
                entity_row(e) for e in silver.execute(select(Entity).where(Entity.id.in_(chunk))).scalars()
            ]
            if rows:
                gold.execute(insert(Entity), rows)

        # --- claims of kept persons: re-point, dedupe ----------------------
        claim_rows: list[dict[str, Any]] = []
        seen_claims: set[tuple[uuid.UUID, str, uuid.UUID | None, str | None, int]] = set()
        referenced: set[uuid.UUID] = set()
        for chunk in _chunked(sorted(kept_members, key=str)):
            for c in silver.execute(
                select(Claim).where(Claim.subject_id.in_(chunk)).order_by(Claim.id)
            ).scalars():
                subject = root(c.subject_id)
                obj = root(c.object_id) if c.object_id is not None else None
                key = (subject, c.predicate, obj, c.value, c.source_id)
                if key in seen_claims:
                    continue  # collapsing duplicates can align identical claims
                seen_claims.add(key)
                if obj is not None and obj not in kept_members:
                    referenced.add(obj)
                claim_rows.append(
                    {
                        "subject_id": subject,
                        "predicate": c.predicate,
                        "object_id": obj,
                        "value": c.value,
                        "source_id": c.source_id,
                        "record_id": c.record_id,
                        "created_at": c.created_at,
                    }
                )

        # --- rule 3: referenced non-person entities (to a fixpoint) --------
        all_other = set(silver.scalars(select(Entity.id).where(Entity.kind != "person")))
        kept_other: set[uuid.UUID] = set()
        frontier = {r for r in referenced if r not in kept_roots}
        while frontier:
            kept_other |= frontier
            next_frontier: set[uuid.UUID] = set()
            for chunk in _chunked(sorted(frontier, key=str)):
                for c in silver.execute(select(Claim).where(Claim.subject_id.in_(chunk))).scalars():
                    obj = root(c.object_id) if c.object_id is not None else None
                    if obj is None or obj in kept_roots or obj in kept_other:
                        continue
                    next_frontier.add(obj)
                    claim_rows.append(
                        {
                            "subject_id": c.subject_id,
                            "predicate": c.predicate,
                            "object_id": obj,
                            "value": c.value,
                            "source_id": c.source_id,
                            "record_id": c.record_id,
                            "created_at": c.created_at,
                        }
                    )
            frontier = next_frontier
        # With rule 3 off, unreferenced non-person entities are kept as well.
        # Joining after the walk (not seeding it) keeps the referenced part of
        # gold — including discovery-edge claims — identical to a curated run;
        # the pass below picks up the extra entities' literal claims.
        if not config.prune_unreferenced:
            kept_other |= {root(e) for e in all_other} - kept_roots
        # own claims of kept non-person entities (literals, e.g. mentioned_in)
        for chunk in _chunked(sorted(kept_other, key=str)):
            for c in silver.execute(
                select(Claim).where(Claim.subject_id.in_(chunk), Claim.object_id.is_(None))
            ).scalars():
                claim_rows.append(
                    {
                        "subject_id": c.subject_id,
                        "predicate": c.predicate,
                        "object_id": None,
                        "value": c.value,
                        "source_id": c.source_id,
                        "record_id": c.record_id,
                        "created_at": c.created_at,
                    }
                )

        for chunk in _chunked(sorted(kept_other, key=str)):
            rows = [
                entity_row(e) for e in silver.execute(select(Entity).where(Entity.id.in_(chunk))).scalars()
            ]
            if rows:
                gold.execute(insert(Entity), rows)

        for i in range(0, len(claim_rows), INSERT_BATCH):
            gold.execute(insert(Claim), claim_rows[i : i + INSERT_BATCH])

        # --- entity records of everything kept, re-pointed ------------------
        record_count = 0
        record_owner_ids = sorted(kept_members | kept_other, key=str)
        for chunk in _chunked(record_owner_ids):
            rows = [
                {
                    "id": r.id,
                    "source_id": r.source_id,
                    "entity_id": root(r.entity_id) if r.entity_id is not None else None,
                    "external_id": r.external_id,
                    "name": r.name,
                    "url": r.url,
                    "raw": r.raw,
                    "first_seen_at": r.first_seen_at,
                    "last_seen_at": r.last_seen_at,
                    "first_run_id": r.first_run_id,
                    "last_run_id": r.last_run_id,
                }
                for r in silver.execute(
                    select(EntityRecord).where(EntityRecord.entity_id.in_(chunk))
                ).scalars()
            ]
            if rows:
                gold.execute(insert(EntityRecord), rows)
                record_count += len(rows)

        # --- works, titles, mentions (composer ids remapped) ---------------
        work_rows = [
            {
                "id": w.id,
                "composer_entity_id": root(w.composer_entity_id) if w.composer_entity_id else None,
                "canonical_title": w.canonical_title,
                "title_key": w.title_key,
                "work_type": w.work_type,
                "opus_number": w.opus_number,
                "catalogue_prefix": w.catalogue_prefix,
                "catalogue_number": w.catalogue_number,
                "musical_key": w.musical_key,
                "number": w.number,
                "created_at": w.created_at,
                "first_ingested_at": w.first_ingested_at,
                "last_ingested_at": w.last_ingested_at,
            }
            for w in silver.execute(select(Work)).scalars()
        ]
        for i in range(0, len(work_rows), INSERT_BATCH):
            gold.execute(insert(Work), work_rows[i : i + INSERT_BATCH])

        title_rows = [
            {
                "id": t.id,
                "work_id": t.work_id,
                "title": t.title,
                "title_key": t.title_key,
                "source_id": t.source_id,
                "first_seen_at": t.first_seen_at,
            }
            for t in silver.execute(select(WorkTitle)).scalars()
        ]
        for i in range(0, len(title_rows), INSERT_BATCH):
            gold.execute(insert(WorkTitle), title_rows[i : i + INSERT_BATCH])

        mention_count = 0
        mention_rows: list[dict[str, Any]] = []
        for m in silver.execute(select(RawWorkMention)).scalars():
            mention_rows.append(
                {
                    "id": m.id,
                    "source_id": m.source_id,
                    "external_id": m.external_id,
                    "raw_composer": m.raw_composer,
                    "raw_title": m.raw_title,
                    "raw": m.raw,
                    "composer_entity_id": root(m.composer_entity_id) if m.composer_entity_id else None,
                    "work_id": m.work_id,
                    "match_status": m.match_status,
                    "match_score": m.match_score,
                    "match_method": m.match_method,
                    "candidate_work_id": m.candidate_work_id,
                    "first_seen_at": m.first_seen_at,
                    "last_seen_at": m.last_seen_at,
                    "first_run_id": m.first_run_id,
                    "last_run_id": m.last_run_id,
                }
            )
            mention_count += 1
        for i in range(0, len(mention_rows), INSERT_BATCH):
            gold.execute(insert(RawWorkMention), mention_rows[i : i + INSERT_BATCH])

        # --- concerts: copy the silver-derived tables, re-pointing people ---
        # `derive_concerts` resolved participants against every person entity;
        # here duplicates collapse to their canonical root, and links to
        # persons that didn't make it into gold are nulled (the verbatim name
        # is always kept).
        gold_entities = kept_roots | kept_other
        concert_rows = [
            {
                "id": c.id,
                "source_id": c.source_id,
                "external_key": c.external_key,
                "date": c.date,
                "venue": c.venue,
                "season": c.season,
                "event_type": c.event_type,
                "url": c.url,
            }
            for c in silver.execute(select(Concert)).scalars()
        ]
        participant_links = 0
        unresolved_names: set[str] = set()
        participant_rows: list[dict[str, Any]] = []
        for p in silver.execute(select(ConcertParticipant)).scalars():
            entity_id = root(p.entity_id) if p.entity_id is not None else None
            if entity_id is not None and entity_id not in gold_entities:
                entity_id = None
            if entity_id is not None:
                participant_links += 1
            else:
                unresolved_names.add(p.name)
            participant_rows.append(
                {
                    "concert_id": p.concert_id,
                    "role": p.role,
                    "name": p.name,
                    "discipline": p.discipline,
                    "entity_id": entity_id,
                }
            )
        concert_work_rows = [
            {"concert_id": cw.concert_id, "mention_id": cw.mention_id}
            for cw in silver.execute(select(ConcertWork)).scalars()
        ]

        for i in range(0, len(concert_rows), INSERT_BATCH):
            gold.execute(insert(Concert), concert_rows[i : i + INSERT_BATCH])
        for i in range(0, len(participant_rows), INSERT_BATCH):
            gold.execute(insert(ConcertParticipant), participant_rows[i : i + INSERT_BATCH])
        for i in range(0, len(concert_work_rows), INSERT_BATCH):
            gold.execute(insert(ConcertWork), concert_work_rows[i : i + INSERT_BATCH])

    gold_engine.dispose()

    return PromoteStats(
        persons_kept=len(kept_roots),
        persons_dropped=len(all_persons) - len(kept_members),
        persons_promoted_by_sitelinks=len(sitelink_roots - evidence_roots),
        duplicates_collapsed=len(kept_members) - len(kept_roots),
        entities_kept_other=len(kept_other),
        entities_pruned=len(all_other - kept_other),
        claims=len(claim_rows),
        records=record_count,
        works=len(work_rows),
        work_titles=len(title_rows),
        mentions=mention_count,
        concerts=len(concert_rows),
        concert_participant_links=participant_links,
        unresolved_participant_names=len(unresolved_names),
    )
