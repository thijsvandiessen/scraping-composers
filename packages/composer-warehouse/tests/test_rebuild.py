"""Tests for rebuilding the silver database from the bucket."""

import json
import uuid
from pathlib import Path

import pytest
from composer_bronze.bucket import LocalBucket, SnapshotManifest
from composer_bronze.scraper import Scraper
from composer_models import (
    Claim,
    Concert,
    Entity,
    PersonMatch,
    RawWorkMention,
    Recording,
    Source,
    Work,
    WorkTitle,
)
from composer_models.db import get_engine
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from composer_schema import SourceClaim
from composer_warehouse.build import read_build_manifest
from composer_warehouse.rebuild import rebuild_silver, replayable_sources, silver_target
from composer_warehouse.testing import FakeSource, mention, perf_mention, person
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

ARCHIVE = ("archive", "https://archive.example")
BERLINPHIL = ("berlinphil", "https://bp.example")
BASE_URLS: dict[str, str] = dict([ARCHIVE, BERLINPHIL])


def base_url_for(source: str) -> str:
    """Stand-in for the CLI/admin resolver: empty for a source it doesn't know."""
    return BASE_URLS.get(source, "")


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

    stats = rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

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
    rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

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

    stats = rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

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
    rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

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

    stats = rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

    assert stats.person_decisions_dropped == 1
    with _session(db_path) as silver:
        assert silver.scalar(select(Entity.id).where(Entity.label == "Ghost")) is None


def test_rebuild_failure_keeps_old_database(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    db_path = tmp_path / "silver.db"
    rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

    # a "complete" documents snapshot whose records are garbage: the replay must
    # fail. It has to look like a document (``_type: entity``) to be picked up at
    # all — a snapshot of any other ``_type`` is a raw-pages one and is skipped.
    bucket.write_records("archive", "run-bad", [{"_type": "entity", "ingested_at": "not-a-date"}])
    manifest = SnapshotManifest.start("archive", "run-bad")
    bucket.write_manifest(manifest.completed(record_count=1))

    with pytest.raises(RuntimeError, match="replaying archive .*run-bad.* failed"):
        rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

    build_manifest = read_build_manifest(db_path)
    assert build_manifest is not None and build_manifest.status == "failed"
    with _session(db_path) as silver:  # the old database is untouched
        assert silver.scalar(select(Entity.id).where(Entity.label == "Bach, Johann Sebastian")) is not None


def test_silver_target_rejects_urls_with_nothing_to_swap(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    with pytest.raises(ValueError, match="sqlite file or a Postgres URL"):
        rebuild_silver(bucket, base_url_for, "mysql://user:pass@host/composers")
    with pytest.raises(ValueError, match="sqlite file or a Postgres URL"):
        silver_target("sqlite://")  # in-memory: nothing to swap


def test_rebuild_skips_a_snapshot_with_no_documents(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    # a fetch that crashed before writing anything: nothing to load, and it must
    # not shadow the source's real snapshot
    manifest = SnapshotManifest.start("archive", "zzz-later-run")
    bucket.write_manifest(manifest.failed("boom"))
    db_path = tmp_path / "silver.db"

    stats = rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

    assert stats.sources_replayed == 2
    assert (
        json.loads((tmp_path / "bucket" / "archive" / "zzz-later-run" / "manifest.json").read_text())[
            "status"
        ]
        == "failed"
    )


def test_rebuild_skips_a_source_whose_snapshots_are_all_raw_pages(tmp_path: Path) -> None:
    """A crawled-but-not-yet-extracted source has nothing a replay could load.

    Regression guard for the reason ``rebuild-silver`` could not simply swap the
    registry for ``bucket.list_sources()``: without the ``documents`` filter the
    replay reaches a raw crawl snapshot and ``deserialize_document`` raises.
    """
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    bucket.write_records("crawled-only", "run-1", [{"_type": "crawl", "url": "https://x.example"}])
    bucket.write_manifest(SnapshotManifest.start("crawled-only", "run-1").completed(record_count=1))
    db_path = tmp_path / "silver.db"

    stats = rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

    assert stats.sources_replayed == 2
    with _session(db_path) as silver:
        assert silver.scalar(select(Source.id).where(Source.name == "crawled-only")) is None


def test_rebuild_replays_every_bucket_source_and_every_document_run(tmp_path: Path) -> None:
    """The replay is driven by the bucket, not by a registry of scrapers.

    Both halves of the #182 regression: a source nobody passed in (a crawl
    config's extracted documents, or an orphan whose adapter was removed) is
    replayed, and a record that only ever appeared in an older snapshot is not
    dropped by taking just the newest one.
    """
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    # "theclassicreview" is a crawl config, not a scraper: no adapter, no
    # base_url, but its extract step wrote documents under its name.
    extracted = FakeSource(records=(person("Gardiner, John Eliot"),), name="theclassicreview")
    Scraper(extracted).fetch_to_bucket(bucket)
    # a second, newer run of the same source that no longer reports Gardiner
    later = FakeSource(records=(person("Hogwood, Christopher"),), name="theclassicreview")
    Scraper(later).fetch_to_bucket(bucket, run_id="zzz-newest")
    db_path = tmp_path / "silver.db"

    stats = rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

    assert stats.sources_replayed == 3
    with _session(db_path) as silver:
        source = silver.scalars(select(Source).where(Source.name == "theclassicreview")).one()
        assert source.base_url == ""  # no adapter and no config: nothing to resolve
        labels = set(silver.scalars(select(Entity.label)).all())
        assert {"Gardiner, John Eliot", "Hogwood, Christopher"} <= labels


def test_rebuild_keeps_recordings_from_a_crawl_derived_source(tmp_path: Path) -> None:
    """The headline #182 symptom: a rebuild used to take recordings to zero.

    Recordings come only from LLM-extracted mentions (``_source: "llm"``,
    ``_kind: "recording"``), which only crawl-config sources produce — precisely
    the sources a registry-driven replay never opened.
    """
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    raw = {
        "_source": "llm",
        "_kind": "recording",
        "record_key": "https://dg.example/album",
        "title": "Beethoven: Symphony No. 9",
        "label": "Deutsche Grammophon",
        "artists": [{"name": "Simon Rattle", "role": "conductor", "discipline": None}],
    }
    extracted = FakeSource(
        records=(
            perf_mention("https://dg.example/album#w0", "Symphony No. 9", "Beethoven", raw),
            person("Simon Rattle", external_id="dg:rattle"),
        ),
        name="deutschegrammophon",
    )
    Scraper(extracted).fetch_to_bucket(bucket)
    db_path = tmp_path / "silver.db"

    stats = rebuild_silver(bucket, base_url_for, f"sqlite:///{db_path}")

    assert stats.recordings == 1
    with _session(db_path) as silver:
        recording = silver.scalars(select(Recording)).one()
        assert recording.title == "Beethoven: Symphony No. 9"


def test_replayable_sources_lists_the_bucket_not_a_registry(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    Scraper(FakeSource(records=(person("Anon"),), name="theclassicreview")).fetch_to_bucket(bucket)
    bucket.write_records("crawled-only", "run-1", [{"_type": "crawl"}])
    bucket.write_manifest(SnapshotManifest.start("crawled-only", "run-1").completed(record_count=1))

    replayable = replayable_sources(bucket)

    assert [name for name, _ in replayable] == ["archive", "berlinphil", "theclassicreview"]
    assert all(len(run_ids) == 1 for _, run_ids in replayable)


# --------------------------------------------------------------------------
# The same rebuild, against Postgres. These prove the whole ingest → dedupe →
# derive stack is dialect-clean, and that the schema swap carries a real
# database rather than a marker table.


@requires_postgres
def test_rebuild_replays_bucket_into_postgres(tmp_path: Path, pg_url: str) -> None:
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)

    stats = rebuild_silver(bucket, base_url_for, pg_url)

    assert stats.sources_replayed == 2
    assert stats.records_seen == 10
    assert stats.persons_auto_linked == 1
    assert stats.concerts == 1

    engine = get_engine(pg_url)
    try:
        with Session(engine) as silver:
            bach = silver.scalars(select(Entity).where(Entity.label == "Bach, Johann Sebastian")).one()
            claims = silver.scalars(select(Claim).where(Claim.subject_id == bach.id)).all()
            assert any(c.predicate == "has_profession" for c in claims)
            short = silver.scalars(select(Entity).where(Entity.label == "Bach, J.S.")).one()
            assert short.canonical_entity_id == bach.id
            assert silver.scalars(select(Concert)).one().date == "1985-03-01"

            # The staging schema was stamped, so the swapped-in database looks
            # migrated rather than hand-made.
            version = silver.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version is not None

            # And the derivations' explicit ids left the sequence usable.
            silver.add(Concert(source_id=1, external_key="manual"))
            silver.commit()
    finally:
        engine.dispose()

    manifest = silver_target(pg_url).read_manifest()
    assert manifest is not None and manifest.status == "completed"


@requires_postgres
def test_rebuild_preserves_human_decisions_on_postgres(tmp_path: Path, pg_url: str) -> None:
    # collect_decisions reads the *live* schema before the staging build
    # starts; on Postgres that is a different schema, not a different file.
    bucket = LocalBucket(tmp_path / "bucket")
    _seed_bucket(bucket)
    rebuild_silver(bucket, base_url_for, pg_url)

    engine = get_engine(pg_url)
    with Session(engine) as silver:
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
    engine.dispose()

    stats = rebuild_silver(bucket, base_url_for, pg_url)

    assert stats.person_decisions_applied == 2
    assert stats.person_decisions_dropped == 0
    assert stats.work_decisions_applied == 1

    engine = get_engine(pg_url)
    try:
        with Session(engine) as silver:
            beethoven = silver.scalars(select(Entity).where(Entity.label == "Beethoven, Ludwig van")).one()
            short = silver.scalars(select(Entity).where(Entity.label == "Beethoven")).one()
            assert short.canonical_entity_id == beethoven.id
            # the rejected pair stays rejected rather than being re-proposed
            mozart = silver.scalars(select(Entity).where(Entity.label == "Mozart")).one()
            rejected = silver.scalars(select(PersonMatch).where(PersonMatch.entity_id == mozart.id)).one()
            assert rejected.status == "rejected"
            assert mozart.canonical_entity_id is None
    finally:
        engine.dispose()
