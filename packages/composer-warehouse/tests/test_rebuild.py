"""Tests for rebuilding the silver database from the bucket."""

import json
import uuid
from pathlib import Path

import pytest
from composer_bronze.bucket import LocalBucket, SnapshotManifest
from composer_bronze.scraper import Scraper
from composer_schema import SourceClaim
from composer_warehouse.build import read_build_manifest
from composer_warehouse.models import (
    Claim,
    Concert,
    Entity,
    PersonMatch,
    RawWorkMention,
    Work,
    WorkTitle,
)
from composer_warehouse.rebuild import rebuild_silver, sqlite_db_path
from composer_warehouse.testing import FakeSource, mention, perf_mention, person
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ARCHIVE = ("archive", "https://archive.example")
BERLINPHIL = ("berlinphil", "https://bp.example")
SOURCES = [ARCHIVE, BERLINPHIL]


def _seed_bucket(bucket: LocalBucket) -> None:
    archive = FakeSource(
        records=(
            person("Bach, Johann Sebastian", SourceClaim("has_profession", "profession", "composer")),
            person("Bach, J.S.", external_id="a:bach-short"),
            person("Beethoven, Ludwig van", external_id="a:beethoven"),
            person("Beethoven", external_id="a:beethoven-short"),
            person("Mozart, Wolfgang Amadeus", external_id="a:mozart"),
            person("Mozart", external_id="a:mozart-short"),
            # similar-but-not-identical titles: the second scores in the review band
            mention("Songs of a Wayfarer", "Mahler, Gustav", "m1"),
            mention("Songs of a Traveller", "Mahler, Gustav", "m2"),
        ),
        name=ARCHIVE[0],
        base_url=ARCHIVE[1],
    )
    berlinphil = FakeSource(
        records=(
            perf_mention(
                "perf:1-1",
                "Ein Heldenleben",
                "Richard Strauss",
                {"concert_id": "1", "date": "1985-03-01", "conductors": ["Karajan, Herbert von"]},
            ),
            person("Karajan, Herbert von", external_id="bp:karajan"),
        ),
        name=BERLINPHIL[0],
        base_url=BERLINPHIL[1],
    )
    for source in (archive, berlinphil):
        Scraper(source).fetch_to_bucket(bucket)


def _session(db_path: Path) -> Session:
    return Session(create_engine(f"sqlite:///{db_path}"))


def test_rebuild_replays_bucket_with_full_fidelity(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    db_path = tmp_path / "silver.db"

    stats = rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    assert stats.sources_replayed == 2
    assert stats.records_seen == 10
    with _session(db_path) as silver:
        # claims exist only in the bucket documents; their presence proves the
        # replay used the full documents, not just the stored records
        bach = silver.scalars(select(Entity).where(Entity.label == "Bach, Johann Sebastian")).one()
        profession = silver.scalars(select(Claim).where(Claim.subject_id == bach.id)).all()
        assert any(c.predicate == "has_profession" for c in profession)
        # the derivation passes ran: dedupe auto-linked the initials pair, concerts exist
        short = silver.scalars(select(Entity).where(Entity.label == "Bach, J.S.")).one()
        assert short.canonical_entity_id == bach.id
        assert silver.scalars(select(Concert)).one().date == "1985-03-01"
    assert stats.persons_auto_linked == 1
    assert stats.concerts == 1
    manifest = read_build_manifest(db_path)
    assert manifest is not None and manifest.status == "completed"
    assert not Path(f"{db_path}.tmp").exists()


def test_rebuild_preserves_human_decisions(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    db_path = tmp_path / "silver.db"
    rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    # simulate the human review decisions the CLI records
    with _session(db_path) as silver:
        beethoven_pair = silver.scalars(
            select(PersonMatch)
            .join(Entity, Entity.id == PersonMatch.entity_id)
            .where(PersonMatch.status == "needs_review", Entity.label == "Beethoven")
        ).one()
        beethoven_pair.status = "accepted"  # person-review --accept
        beethoven_pair.entity.canonical_entity_id = beethoven_pair.canonical_entity_id
        mozart_pair = silver.scalars(
            select(PersonMatch)
            .join(Entity, Entity.id == PersonMatch.entity_id)
            .where(PersonMatch.status == "needs_review", Entity.label == "Mozart")
        ).one()
        mozart_pair.status = "rejected"  # person-review --reject
        mozart_id, mozart_canonical_id = mozart_pair.entity_id, mozart_pair.canonical_entity_id

        # review --new: create a distinct work from the flagged mention
        flagged = silver.scalars(
            select(RawWorkMention).where(RawWorkMention.match_status == "needs_review")
        ).one()
        new = Work(
            id=uuid.uuid4(),
            composer_entity_id=flagged.composer_entity_id,
            canonical_title=flagged.raw_title,
            title_key="songs of a traveller",
        )
        silver.add(new)
        flagged.work_id = new.id
        flagged.match_status = "manual_matched"
        flagged.match_method = "manual"
        silver.commit()

    stats = rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    assert stats.person_decisions_applied == 2
    assert stats.person_decisions_dropped == 0
    assert stats.work_decisions_applied == 1
    with _session(db_path) as silver:
        # accepted: the link is back
        beethoven_short = silver.scalars(select(Entity).where(Entity.label == "Beethoven")).one()
        assert beethoven_short.canonical_entity_id is not None
        # rejected: remembered, not re-proposed, not linked
        mozart_rows = silver.scalars(
            select(PersonMatch).where(
                PersonMatch.entity_id == mozart_id,
                PersonMatch.canonical_entity_id == mozart_canonical_id,
            )
        ).all()
        assert [m.status for m in mozart_rows] == ["rejected"]
        mozart = silver.scalars(select(Entity).where(Entity.label == "Mozart")).one()
        assert mozart.canonical_entity_id is None
        # manual work match: re-created (fresh uuid) and re-linked, with the alias
        traveller = silver.scalars(
            select(RawWorkMention).where(RawWorkMention.raw_title == "Songs of a Traveller")
        ).one()
        assert traveller.match_status == "manual_matched"
        work = silver.get(Work, traveller.work_id)
        assert work is not None and work.title_key == "songs of a traveller"
        aliases = silver.scalars(select(WorkTitle.title_key).where(WorkTitle.work_id == work.id)).all()
        assert "songs of a traveller" in aliases


def test_rebuild_drops_decisions_for_vanished_entities(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    db_path = tmp_path / "silver.db"
    rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    with _session(db_path) as silver:
        # an accepted pair for entities no source reports (anymore)
        ghost, ghost_canonical = uuid.uuid4(), uuid.uuid4()
        silver.add(Entity(id=ghost, kind="person", dedup_key="ghost", label="Ghost"))
        silver.add(Entity(id=ghost_canonical, kind="person", dedup_key="ghost g", label="Ghost, Gone"))
        silver.add(
            PersonMatch(
                entity_id=ghost,
                canonical_entity_id=ghost_canonical,
                score=1.0,
                method="manual",
                status="accepted",
            )
        )
        silver.commit()

    stats = rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    assert stats.person_decisions_dropped == 1
    with _session(db_path) as silver:
        assert silver.scalar(select(Entity.id).where(Entity.label == "Ghost")) is None


def test_rebuild_failure_keeps_old_database(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    db_path = tmp_path / "silver.db"
    rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    # a "complete" snapshot whose records are garbage: the replay must fail
    bucket.write_records("archive", "run-bad", [{"_type": "nonsense"}])
    manifest = SnapshotManifest.start("archive", "run-bad")
    bucket.write_manifest(manifest.completed(record_count=1))

    with pytest.raises(RuntimeError, match="replaying archive/run-bad failed"):
        rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    build_manifest = read_build_manifest(db_path)
    assert build_manifest is not None and build_manifest.status == "failed"
    with _session(db_path) as silver:  # the old database is untouched
        assert silver.scalar(select(Entity.id).where(Entity.label == "Bach, Johann Sebastian")) is not None


def test_rebuild_requires_a_sqlite_file_url(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    with pytest.raises(ValueError, match="file-backed sqlite"):
        rebuild_silver(bucket, SOURCES, "postgresql+psycopg://user:pass@host:5432/composers")
    with pytest.raises(ValueError, match="file-backed sqlite"):
        sqlite_db_path("sqlite://")  # in-memory: nothing to swap


def test_rebuild_failed_source_snapshots_are_skipped(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    # a failed fetch must not shadow the older complete snapshot
    manifest = SnapshotManifest.start("archive", "zzz-later-run")
    bucket.write_manifest(manifest.failed("boom"))
    db_path = tmp_path / "silver.db"

    stats = rebuild_silver(bucket, SOURCES, f"sqlite:///{db_path}")

    assert stats.sources_replayed == 2
    assert (
        json.loads((tmp_path / "bucket" / "archive" / "zzz-later-run" / "manifest.json").read_text())[
            "status"
        ]
        == "failed"
    )
