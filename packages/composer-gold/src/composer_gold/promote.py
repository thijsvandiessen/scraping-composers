"""Rebuild the gold database from bronze, applying the curation rules."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from composer_warehouse.normalize import dedup_key
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
class GoldManifest:
    status: str  # running | completed | failed
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    stats: dict[str, int] = field(default_factory=dict)

    @classmethod
    def start(cls) -> GoldManifest:
        return cls(status="running", started_at=datetime.now(UTC).isoformat())

    def completed(self, stats: PromoteStats) -> GoldManifest:
        return replace(
            self, status="completed", finished_at=datetime.now(UTC).isoformat(), stats=asdict(stats)
        )

    def failed(self, error: str) -> GoldManifest:
        return replace(self, status="failed", finished_at=datetime.now(UTC).isoformat(), error=error)


def _manifest_path(gold_path: str | Path) -> Path:
    return Path(f"{gold_path}.manifest.json")


def write_gold_manifest(gold_path: str | Path, manifest: GoldManifest) -> None:
    path = _manifest_path(gold_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), ensure_ascii=False), encoding="utf-8")


def read_gold_manifest(gold_path: str | Path) -> GoldManifest | None:
    path = _manifest_path(gold_path)
    if not path.exists():
        return None
    return GoldManifest(**json.loads(path.read_text(encoding="utf-8")))


def _chunked(ids: list[Any]) -> Iterable[list[Any]]:
    for i in range(0, len(ids), IN_CHUNK):
        yield ids[i : i + IN_CHUNK]


_DDMMYYYY = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


def _iso_date(value: str | None) -> str | None:
    """Normalize DD-MM-YYYY (concertgebouw) to ISO; pass other formats through."""
    if not value:
        return None
    match = _DDMMYYYY.match(value)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return value


@dataclass(frozen=True)
class _ConcertFields:
    """One mention's concert-level payload, in a source-independent shape."""

    external_key: str
    date: str | None
    venue: str | None
    season: str | None
    event_type: str | None
    url: str | None
    conductors: tuple[str, ...]
    soloists: tuple[tuple[str, str | None], ...]  # (name, discipline)


def _soloists(raw: dict[str, Any]) -> tuple[tuple[str, str | None], ...]:
    # all three sources report soloists as {"name": ..., "discipline": ...}
    return tuple(
        (s["name"], s.get("discipline"))
        for s in raw.get("soloists") or []
        if isinstance(s, dict) and s.get("name")
    )


def _concert_fields(source_name: str, raw: dict[str, Any]) -> _ConcertFields | None:
    """Concert identity and fields for one mention's payload.

    Each performance source encodes concert identity differently; unknown
    sources return None and are skipped.
    """
    if source_name == "concertgebouw_archive":
        date = _iso_date(raw.get("date"))
        city = raw.get("city")
        if not date:
            return None
        conductor = raw.get("conductor")
        return _ConcertFields(
            external_key=f"{date}|{city or ''}",
            date=date,
            venue=city,
            season=None,
            event_type=None,
            url=None,
            conductors=(conductor,) if conductor else (),
            soloists=_soloists(raw),
        )
    if source_name == "nyphil":
        program = raw.get("programID")
        date = raw.get("date")
        if not program or not date:
            return None
        venue = ", ".join(part for part in (raw.get("venue"), raw.get("location")) if part) or None
        return _ConcertFields(
            external_key=f"{program}|{date}",
            date=date,
            venue=venue,
            season=raw.get("season"),
            event_type=raw.get("eventType"),
            url=None,
            conductors=tuple(raw.get("conductors") or ()),
            soloists=_soloists(raw),
        )
    if source_name == "berlinphil":
        concert_id = raw.get("concert_id")
        if not concert_id:
            return None
        return _ConcertFields(
            external_key=str(concert_id),
            date=raw.get("date"),
            venue=None,
            season=raw.get("season"),
            event_type=None,
            url=raw.get("url"),
            conductors=tuple(raw.get("conductors") or ()),
            soloists=_soloists(raw),
        )
    return None


def _resolve_roots(bronze: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Map every canonical-linked person to its transitive canonical root."""
    links: dict[uuid.UUID, uuid.UUID] = {
        entity_id: canonical_id
        for entity_id, canonical_id in bronze.execute(
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
    bronze: Session,
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
    for subject_id, value in bronze.execute(
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


def promote(bronze: Session, gold_path: str | Path, *, min_sitelinks: int | None = None) -> PromoteStats:
    """Rebuild the gold database at ``gold_path`` from the bronze session.

    Builds into ``{gold_path}.tmp`` and atomically swaps it in, so readers
    never see a half-built database. Progress and outcome land in
    ``{gold_path}.manifest.json``.

    ``min_sitelinks`` is an optional extra promotion signal: when set, a person
    whose Wikipedia sitelink count reaches it is kept even without the
    performance/work evidence rule 1 otherwise requires (see ``_build``). ``None``
    leaves promotion unchanged.
    """
    manifest = GoldManifest.start()
    write_gold_manifest(gold_path, manifest)
    try:
        stats = _build(bronze, Path(f"{gold_path}.tmp"), min_sitelinks=min_sitelinks)
        os.replace(f"{gold_path}.tmp", gold_path)
    except Exception as exc:
        write_gold_manifest(gold_path, manifest.failed(f"{type(exc).__name__}: {exc}"))
        raise
    write_gold_manifest(gold_path, manifest.completed(stats))
    log.info("gold promoted to %s: %s", gold_path, stats)
    return stats


def _build(bronze: Session, tmp_path: Path, *, min_sitelinks: int | None = None) -> PromoteStats:
    tmp_path.unlink(missing_ok=True)
    gold_engine = create_engine(f"sqlite:///{tmp_path}")
    Base.metadata.create_all(gold_engine)

    # --- rule 2 groundwork: duplicate clusters -----------------------------
    roots = _resolve_roots(bronze)

    def root(entity_id: uuid.UUID) -> uuid.UUID:
        return roots.get(entity_id, entity_id)

    # --- rule 1: persons with performance/work evidence --------------------
    mention_composers = set(
        bronze.scalars(
            select(RawWorkMention.composer_entity_id)
            .where(RawWorkMention.composer_entity_id.is_not(None))
            .distinct()
        )
    )
    perf_sources = select(RawWorkMention.source_id).distinct().scalar_subquery()
    archive_reported = set(
        bronze.scalars(
            select(EntityRecord.entity_id)
            .where(EntityRecord.source_id.in_(perf_sources), EntityRecord.entity_id.is_not(None))
            .distinct()
        )
    )
    evidence = mention_composers | archive_reported

    all_persons = set(bronze.scalars(select(Entity.id).where(Entity.kind == "person")))
    evidence_roots = {root(p) for p in all_persons if p in evidence}

    # --- extra signal: culturally significant persons by sitelink count -----
    # Wikipedia sitelink count (from Wikidata) is a proxy for significance. When
    # a threshold is set, a person clearing it is promoted even without the
    # performance/work evidence above; this only ever adds persons, never drops.
    sitelink_roots = _sitelink_roots(bronze, root, all_persons, min_sitelinks)

    kept_roots = evidence_roots | sitelink_roots
    kept_members = {p for p in all_persons if root(p) in kept_roots}

    with gold_engine.begin() as gold:
        # --- FK targets: sources and runs, wholesale -----------------------
        source_names: dict[int, str] = {}
        for row in bronze.execute(select(Source)).scalars():
            source_names[row.id] = row.name
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
            for r in bronze.execute(select(IngestRun)).scalars()
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
                entity_row(e) for e in bronze.execute(select(Entity).where(Entity.id.in_(chunk))).scalars()
            ]
            if rows:
                gold.execute(insert(Entity), rows)

        # --- claims of kept persons: re-point, dedupe ----------------------
        claim_rows: list[dict[str, Any]] = []
        seen_claims: set[tuple[uuid.UUID, str, uuid.UUID | None, str | None, int]] = set()
        referenced: set[uuid.UUID] = set()
        for chunk in _chunked(sorted(kept_members, key=str)):
            for c in bronze.execute(
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
        kept_other: set[uuid.UUID] = set()
        frontier = {r for r in referenced if r not in kept_roots}
        while frontier:
            kept_other |= frontier
            next_frontier: set[uuid.UUID] = set()
            for chunk in _chunked(sorted(frontier, key=str)):
                for c in bronze.execute(select(Claim).where(Claim.subject_id.in_(chunk))).scalars():
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
        # own claims of kept non-person entities (literals, e.g. mentioned_in)
        for chunk in _chunked(sorted(kept_other, key=str)):
            for c in bronze.execute(
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
                entity_row(e) for e in bronze.execute(select(Entity).where(Entity.id.in_(chunk))).scalars()
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
                for r in bronze.execute(
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
            for w in bronze.execute(select(Work)).scalars()
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
            for t in bronze.execute(select(WorkTitle)).scalars()
        ]
        for i in range(0, len(title_rows), INSERT_BATCH):
            gold.execute(insert(WorkTitle), title_rows[i : i + INSERT_BATCH])

        mention_count = 0
        mention_rows: list[dict[str, Any]] = []
        for m in bronze.execute(select(RawWorkMention)).scalars():
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

        # --- concerts: derive from the mentions' raw performance context ----
        # Every kept person's dedup key resolves to its gold (canonical) id, so
        # conductor names match regardless of which duplicate spelling appears.
        person_by_key: dict[str, uuid.UUID] = {}
        for chunk in _chunked(sorted(kept_members, key=str)):
            for member_id, member_key in bronze.execute(
                select(Entity.id, Entity.dedup_key).where(Entity.id.in_(chunk))
            ).tuples():
                person_by_key[member_key] = root(member_id)

        concerts: dict[tuple[int, str], dict[str, Any]] = {}
        for m_row in mention_rows:
            source_name = source_names.get(m_row["source_id"], "")
            fields = _concert_fields(source_name, json.loads(m_row["raw"]))
            if fields is None:
                continue
            concert = concerts.setdefault(
                (m_row["source_id"], fields.external_key),
                {
                    "date": fields.date,
                    "venue": fields.venue,
                    "season": fields.season,
                    "event_type": fields.event_type,
                    "url": fields.url,
                    "conductors": set(),
                    "soloists": {},  # name -> discipline (first non-null wins)
                    "mention_ids": [],
                },
            )
            concert["conductors"].update(fields.conductors)
            for soloist_name, discipline in fields.soloists:
                if concert["soloists"].get(soloist_name) is None:
                    concert["soloists"][soloist_name] = discipline
            concert["mention_ids"].append(m_row["id"])

        concert_rows: list[dict[str, Any]] = []
        participant_rows: list[dict[str, Any]] = []
        concert_work_rows: list[dict[str, Any]] = []
        participant_links = 0
        unresolved_names: set[str] = set()

        def add_participant(concert_id: int, role: str, name: str, discipline: str | None) -> None:
            nonlocal participant_links
            resolved = person_by_key.get(dedup_key(name))
            if resolved is not None:
                participant_links += 1
            else:
                unresolved_names.add(name)
            participant_rows.append(
                {
                    "concert_id": concert_id,
                    "role": role,
                    "name": name,
                    "discipline": discipline,
                    "entity_id": resolved,
                }
            )

        for concert_id, ((source_id, external_key), data) in enumerate(sorted(concerts.items()), start=1):
            concert_rows.append(
                {
                    "id": concert_id,
                    "source_id": source_id,
                    "external_key": external_key,
                    "date": data["date"],
                    "venue": data["venue"],
                    "season": data["season"],
                    "event_type": data["event_type"],
                    "url": data["url"],
                }
            )
            for name in sorted(data["conductors"]):
                add_participant(concert_id, "conductor", name, None)
            for name in sorted(data["soloists"]):
                add_participant(concert_id, "soloist", name, data["soloists"][name])
            concert_work_rows.extend(
                {"concert_id": concert_id, "mention_id": mention_id} for mention_id in data["mention_ids"]
            )

        for i in range(0, len(concert_rows), INSERT_BATCH):
            gold.execute(insert(Concert), concert_rows[i : i + INSERT_BATCH])
        for i in range(0, len(participant_rows), INSERT_BATCH):
            gold.execute(insert(ConcertParticipant), participant_rows[i : i + INSERT_BATCH])
        for i in range(0, len(concert_work_rows), INSERT_BATCH):
            gold.execute(insert(ConcertWork), concert_work_rows[i : i + INSERT_BATCH])

    gold_engine.dispose()

    all_other = set(bronze.scalars(select(Entity.id).where(Entity.kind != "person")))
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
