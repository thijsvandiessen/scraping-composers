# pylint: disable=too-many-lines
"""Tests for CLI query helpers (claim provenance lookup) and CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest
from composer_cli import main
from composer_cli.ingest_cmds import cmd_derive_concerts, cmd_fetch, cmd_process, cmd_rebuild_silver
from composer_cli.person_cmds import cmd_dedupe_persons, cmd_person_review
from composer_cli.query_cmds import ClaimFilters, cmd_claims, cmd_runs, cmd_stats, entity_claims
from composer_cli.work_cmds import cmd_rematch, cmd_review, cmd_works
from composer_schema import SourceClaim
from composer_warehouse.db import get_engine, init_db
from composer_warehouse.models import Concert, Entity, PersonMatch, RawWorkMention, Work
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention, person
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _ns(**kwargs: object) -> argparse.Namespace:
    """Build a minimal Namespace with defaults a CLI command expects."""
    return argparse.Namespace(**{"database_url": None, "verbose": False, **kwargs})


def _ingest_two_sources_disagreeing(session: Session) -> None:
    # same person from two sources with a conflicting birth date and a shared one
    a = FakeSource(
        records=(
            person(
                "Abert, Johann Joseph",
                SourceClaim("has_profession", "profession", "composer"),
                SourceClaim("born_on", value="1832"),
                external_id="cg:990",
            ),
        ),
        name="concertgebouw_archive",
    )
    b = FakeSource(
        records=(
            person(
                "Johann Joseph Abert",  # different formatting, same dedup key
                SourceClaim("has_profession", "profession", "composer"),
                SourceClaim("born_on", value="1832-09-20"),
                external_id="Q123",
            ),
        ),
        name="wikidata",
    )
    ingest_source(session, a)
    ingest_source(session, b)


def test_entity_claims_attributes_each_value_to_its_source(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((entity, rows),) = entity_claims(session, "Abert, Johann Joseph")
    assert entity.label == "Abert, Johann Joseph"  # deduped to one entity

    born = [(value, source) for predicate, value, _obj, source, _rec in rows if predicate == "born_on"]
    assert born == [("1832", "concertgebouw_archive"), ("1832-09-20", "wikidata")]


def test_entity_claims_filters_by_predicate_and_source(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((_, rows),) = entity_claims(session, "Abert", ClaimFilters(predicate="born_on", source="wikidata"))
    assert [(r[0], r[1], r[3]) for r in rows] == [("born_on", "1832-09-20", "wikidata")]


def test_entity_claims_carries_record_provenance(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((_, rows),) = entity_claims(session, "Abert", ClaimFilters(predicate="born_on"))
    # every row points back to the raw record it was extracted from
    assert all(record_id is not None for *_rest, record_id in rows)


def test_entity_claims_collapses_identical_assertions_from_one_source(session: Session) -> None:
    # one source asserting the same fact via two records keeps a single claim row
    source = FakeSource(
        records=(
            person(
                "Bach, Johann Sebastian",
                SourceClaim("has_profession", "profession", "composer"),
                external_id="a",
            ),
            person(
                "Johann Sebastian Bach",
                SourceClaim("has_profession", "profession", "composer"),
                external_id="b",
            ),
        ),
        name="wikidata",
    )
    ingest_source(session, source)

    ((_, rows),) = entity_claims(session, "Bach, Johann Sebastian", ClaimFilters(predicate="has_profession"))
    assert len(rows) == 1
    predicate, _value, object_label, source_name, _rec = rows[0]
    assert (predicate, object_label, source_name) == ("has_profession", "composer", "wikidata")


def test_entity_claims_returns_empty_for_unknown_name(session: Session) -> None:
    assert entity_claims(session, "Nobody, At All") == []


# ---------------------------------------------------------------------------
# cmd_stats
# ---------------------------------------------------------------------------


def test_cmd_stats_empty_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    rc = cmd_stats(_ns(database_url=db_url))
    assert rc == 0
    out = capsys.readouterr().out
    assert "entities (deduplicated): 0" in out
    assert "claims:" in out


def test_cmd_stats_shows_entity_and_claim_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    factory = init_db(get_engine(db_url))
    with factory() as session:
        ingest_source(
            session,
            FakeSource(
                records=(
                    person("Bach, Johann Sebastian", SourceClaim("has_profession", "profession", "composer")),
                )
            ),
        )

    rc = cmd_stats(_ns(database_url=db_url))
    assert rc == 0
    out = capsys.readouterr().out
    assert "person: 1" in out
    assert "fake:" in out


# ---------------------------------------------------------------------------
# works / review / rematch
# ---------------------------------------------------------------------------


def _ingest(db_url: str, *records: object) -> None:
    factory = init_db(get_engine(db_url))
    with factory() as session:
        ingest_source(session, FakeSource(records=records))  # pyright: ignore[reportArgumentType]


def test_cmd_stats_shows_work_and_mention_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest(db_url, mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van"))

    assert cmd_stats(_ns(database_url=db_url)) == 0
    out = capsys.readouterr().out
    assert "works (resolved):        1" in out
    assert "work mentions:           1" in out
    assert "created: 1" in out


def test_cmd_works_lists_work_with_aliases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest(
        db_url,
        mention("Symphony No. 5 in C minor, Op. 67", "Beethoven, Ludwig van", "m1"),
        mention("Sinfonie Nr. 5 c-moll, op. 67", "Ludwig van Beethoven", "m2"),
    )

    assert cmd_works(_ns(database_url=db_url, name="Beethoven", limit=20)) == 0
    out = capsys.readouterr().out
    assert "Symphony No. 5 in C minor, Op. 67" in out
    assert "mentions: 2" in out
    assert "alias: Sinfonie Nr. 5 c-moll, op. 67" in out


def test_cmd_works_returns_1_for_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(get_engine(db_url))
    assert cmd_works(_ns(database_url=db_url, name="Nobody", limit=20)) == 1
    assert "no work" in capsys.readouterr().out


def test_cmd_review_lists_flagged_mentions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest(
        db_url,
        mention("Songs of a Wayfarer", "Mahler, Gustav", "m1"),
        mention("Songs of a Traveller", "Mahler, Gustav", "m2"),
    )

    assert cmd_review(_ns(database_url=db_url, limit=20, accept=None, new=None)) == 0
    assert "Songs of a Traveller" in capsys.readouterr().out


def test_cmd_review_new_creates_work_from_mention(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest(
        db_url,
        mention("Songs of a Wayfarer", "Mahler, Gustav", "m1"),
        mention("Songs of a Traveller", "Mahler, Gustav", "m2"),
    )
    factory = init_db(get_engine(db_url))
    with factory() as session:
        review_id = session.scalar(
            select(RawWorkMention.id).where(RawWorkMention.match_status == "needs_review")
        )
        before = session.scalar(select(func.count(Work.id))) or 0

    assert cmd_review(_ns(database_url=db_url, limit=20, accept=None, new=review_id)) == 0

    with factory() as session:
        assert session.scalar(select(func.count(Work.id))) == before + 1
        row = session.get(RawWorkMention, review_id)
        assert row is not None and row.match_status == "manual_matched"
        assert row.work_id is not None


def test_cmd_rematch_processes_pending_mentions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest(
        db_url,
        mention("Songs of a Wayfarer", "Mahler, Gustav", "m1"),
        mention("Songs of a Traveller", "Mahler, Gustav", "m2"),
    )
    assert cmd_rematch(_ns(database_url=db_url)) == 0
    assert "re-matched" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# dedupe-persons / person-review
# ---------------------------------------------------------------------------


def _ingest_varied_people(db_url: str) -> None:
    _ingest(
        db_url,
        person("Bach, J.S."),  # auto-links to the full name (initials)
        person("Bach, Johann Sebastian"),
        person("Beethoven"),  # surname-only -> review
        person("Beethoven, Ludwig van"),
    )


def test_cmd_dedupe_persons_reports_links_and_reviews(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest_varied_people(db_url)

    assert cmd_dedupe_persons(_ns(database_url=db_url)) == 0
    out = capsys.readouterr().out
    assert "auto-linked 1" in out
    assert "1 pair(s) need review" in out

    factory = init_db(get_engine(db_url))
    with factory() as session:
        assert (
            session.scalar(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None))) == 1
        )


def test_cmd_stats_shows_person_dedup_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest_varied_people(db_url)
    cmd_dedupe_persons(_ns(database_url=db_url))
    capsys.readouterr()  # drop dedupe output

    assert cmd_stats(_ns(database_url=db_url)) == 0
    out = capsys.readouterr().out
    assert "person duplicates linked: 1" in out
    assert "person matches to review: 1" in out


def test_cmd_person_review_lists_and_accepts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest_varied_people(db_url)
    cmd_dedupe_persons(_ns(database_url=db_url))

    assert cmd_person_review(_ns(database_url=db_url, limit=20, accept=None, reject=None)) == 0
    assert "Beethoven" in capsys.readouterr().out

    factory = init_db(get_engine(db_url))
    with factory() as session:
        match = session.scalars(select(PersonMatch).where(PersonMatch.status == "needs_review")).one()
        match_id, dup_id = match.id, match.entity_id

    assert cmd_person_review(_ns(database_url=db_url, limit=20, accept=match_id, reject=None)) == 0

    with factory() as session:
        accepted = session.get(PersonMatch, match_id)
        assert accepted is not None and accepted.status == "accepted"
        dup = session.get(Entity, dup_id)
        assert dup is not None and dup.canonical_entity_id is not None


def test_cmd_person_review_reject(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    _ingest_varied_people(db_url)
    cmd_dedupe_persons(_ns(database_url=db_url))

    factory = init_db(get_engine(db_url))
    with factory() as session:
        match_id = session.scalar(select(PersonMatch.id).where(PersonMatch.status == "needs_review"))

    assert cmd_person_review(_ns(database_url=db_url, limit=20, accept=None, reject=match_id)) == 0
    with factory() as session:
        rejected = session.get(PersonMatch, match_id)
        assert rejected is not None and rejected.status == "rejected"
        assert (
            session.scalar(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None))) == 1
        )  # only the auto-linked Bach pair, not the rejected one


# ---------------------------------------------------------------------------
# cmd_derive_concerts
# ---------------------------------------------------------------------------


def test_cmd_derive_concerts_builds_concert_tables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    factory = init_db(get_engine(db_url))
    with factory() as session:
        # a real performance-source name: concert derivation is per-source
        ingest_source(
            session,
            FakeSource(
                records=(
                    perf_mention(
                        "perf:1-1",
                        "Ein Heldenleben",
                        "Richard Strauss",
                        {"concert_id": "1", "date": "1985-03-01", "conductors": ["Karajan, Herbert von"]},
                    ),
                    person("Karajan, Herbert von"),
                ),
                name="berlinphil",
                base_url="https://bp.example",
            ),
        )

    assert cmd_derive_concerts(_ns(database_url=db_url)) == 0
    out = capsys.readouterr().out
    assert "derived 1 concerts" in out
    assert "participant links      1" in out

    with factory() as session:
        concert = session.scalars(select(Concert)).one()
        assert concert.date == "1985-03-01"


# ---------------------------------------------------------------------------
# cmd_fetch / cmd_process
# ---------------------------------------------------------------------------


def test_cmd_fetch_then_process_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_scrapers import REGISTRY

    db_url = f"sqlite:///{tmp_path}/test.db"
    bucket_path = str(tmp_path / "bucket")
    fake = FakeSource(records=(person("Bach, Johann Sebastian"),), name="fake")
    monkeypatch.setitem(REGISTRY, "fake", fake)

    rc = cmd_fetch(_ns(source="fake", max_pages=None, bucket_path=bucket_path))
    assert rc == 0
    ndjson_files = list((tmp_path / "bucket" / "fake").glob("*/records.ndjson"))
    assert len(ndjson_files) == 1  # raw snapshot on disk before anything touches the DB
    manifest = json.loads((ndjson_files[0].parent / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["record_count"] == 1

    rc = cmd_process(_ns(database_url=db_url, source="fake", run_id=None, bucket_path=bucket_path))
    assert rc == 0
    factory = init_db(get_engine(db_url))
    with factory() as session:
        entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()
        assert entity.label == "Bach, Johann Sebastian"


def test_cmd_fetch_returns_1_on_source_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_scrapers import REGISTRY

    fake = FakeSource(records=(person("Mozart"), person("Haydn")), name="fake", fail_after=1)
    monkeypatch.setitem(REGISTRY, "fake", fake)

    rc = cmd_fetch(_ns(source="fake", max_pages=None, bucket_path=str(tmp_path / "bucket")))
    assert rc == 1
    # the crash is recorded on disk, so the snapshot can never be mistaken for complete
    manifests = list((tmp_path / "bucket" / "fake").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["status"] == "failed"
    assert "source exploded" in manifest["error"]


def test_cmd_process_default_skips_incomplete_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from composer_scrapers import REGISTRY

    db_url = f"sqlite:///{tmp_path}/test.db"
    bucket_path = str(tmp_path / "bucket")
    good = FakeSource(records=(person("Bach, Johann Sebastian"),), name="fake")
    monkeypatch.setitem(REGISTRY, "fake", good)
    assert cmd_fetch(_ns(source="fake", max_pages=None, bucket_path=bucket_path)) == 0

    # a later fetch crashes -> failed manifest; default process must skip it
    bad = FakeSource(records=(person("Mozart"),), name="fake", fail_after=0)
    monkeypatch.setitem(REGISTRY, "fake", bad)
    assert cmd_fetch(_ns(source="fake", max_pages=None, bucket_path=bucket_path)) == 1
    monkeypatch.setitem(REGISTRY, "fake", good)

    rc = cmd_process(_ns(database_url=db_url, source="fake", run_id=None, bucket_path=bucket_path))
    assert rc == 0
    factory = init_db(get_engine(db_url))
    with factory() as session:
        labels = session.scalars(select(Entity.label).where(Entity.kind == "person")).all()
        assert labels == ["Bach, Johann Sebastian"]  # the completed snapshot, not the crashed one


def test_cmd_process_returns_1_without_fetched_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_scrapers import REGISTRY

    monkeypatch.setitem(REGISTRY, "fake", FakeSource(records=(), name="fake"))
    rc = cmd_process(
        _ns(
            database_url=f"sqlite:///{tmp_path}/test.db",
            source="fake",
            run_id=None,
            bucket_path=str(tmp_path / "bucket"),
        )
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# cmd_rebuild_silver
# ---------------------------------------------------------------------------


def test_cmd_rebuild_silver_replays_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from composer_scrapers import REGISTRY

    db_url = f"sqlite:///{tmp_path}/test.db"
    bucket_path = str(tmp_path / "bucket")
    fake = FakeSource(records=(person("Bach, Johann Sebastian"),), name="fake")
    monkeypatch.setitem(REGISTRY, "fake", fake)
    assert cmd_fetch(_ns(source="fake", max_pages=None, bucket_path=bucket_path)) == 0

    rc = cmd_rebuild_silver(_ns(database_url=db_url, bucket_path=bucket_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "silver rebuilt from the bucket" in out
    assert "sources replayed" in out

    factory = init_db(get_engine(db_url))
    with factory() as session:
        entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()
        assert entity.label == "Bach, Johann Sebastian"


def test_cmd_rebuild_silver_rejects_non_sqlite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cmd_rebuild_silver(
        _ns(
            database_url="postgresql+psycopg://user:pass@host:5432/composers",
            bucket_path=str(tmp_path / "bucket"),
        )
    )
    assert rc == 1
    assert "sqlite" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_claims
# ---------------------------------------------------------------------------


def test_cmd_claims_prints_entity_and_claims(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    factory = init_db(get_engine(db_url))
    with factory() as session:
        ingest_source(
            session,
            FakeSource(
                records=(person("Bach, Johann Sebastian", SourceClaim("born_on", value="1685-03-21")),)
            ),
        )

    rc = cmd_claims(_ns(database_url=db_url, name="Bach", kind=None, predicate=None, source=None, limit=10))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bach, Johann Sebastian" in out
    assert "born_on" in out
    assert "1685-03-21" in out


def test_cmd_claims_returns_1_for_unknown_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(get_engine(db_url))

    rc = cmd_claims(_ns(database_url=db_url, name="Nobody", kind=None, predicate=None, source=None, limit=10))
    assert rc == 1
    assert "no entity" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_runs
# ---------------------------------------------------------------------------


def test_cmd_runs_empty_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(get_engine(db_url))

    rc = cmd_runs(_ns(database_url=db_url, limit=20))
    assert rc == 0
    assert "no ingest runs" in capsys.readouterr().out


def test_cmd_runs_shows_run_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    factory = init_db(get_engine(db_url))
    with factory() as session:
        ingest_source(session, FakeSource(records=(person("Haydn, Joseph"),), name="wikidata"))

    rc = cmd_runs(_ns(database_url=db_url, limit=20))
    assert rc == 0
    out = capsys.readouterr().out
    assert "wikidata" in out
    assert "completed" in out


# ---------------------------------------------------------------------------
# main() argument parsing
# ---------------------------------------------------------------------------


def test_main_exits_nonzero_without_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["composer-ingest"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code != 0


def test_main_routes_to_stats_subcommand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(get_engine(db_url))
    monkeypatch.setattr(sys, "argv", ["composer-ingest", "--database-url", db_url, "stats"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0


def test_main_routes_to_claims_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    factory = init_db(get_engine(db_url))
    with factory() as session:
        bach = person("Bach, J.S.", SourceClaim("born_on", value="1685"))
        ingest_source(session, FakeSource(records=(bach,)))

    monkeypatch.setattr(sys, "argv", ["composer-ingest", "--database-url", db_url, "claims", "Bach"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "born_on" in capsys.readouterr().out


def test_main_routes_to_runs_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    factory = init_db(get_engine(db_url))
    with factory() as session:
        ingest_source(session, FakeSource(records=(person("Beethoven"),), name="wikidata"))

    monkeypatch.setattr(sys, "argv", ["composer-ingest", "--database-url", db_url, "runs"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "wikidata" in capsys.readouterr().out


def test_get_engine_reads_database_url_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{tmp_path}/env.db"
    from composer_config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    engine = get_engine()  # no explicit URL — falls back to env var
    assert str(engine.url) == db_url


def test_promote_cli_passes_min_referrers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_cli import ingest_cmds
    from composer_gold import PromoteConfig, PromoteStats

    captured: list[PromoteConfig] = []

    def fake_promote(session: object, gold_path: str, config: PromoteConfig) -> PromoteStats:
        captured.append(config)
        return PromoteStats()

    monkeypatch.setattr(ingest_cmds, "promote", fake_promote)
    db_url = f"sqlite:///{tmp_path}/silver.db"
    init_db(get_engine(db_url))  # empty silver: derive_* and promote run over it
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "composer-ingest",
            "--database-url",
            db_url,
            "promote",
            "--gold-path",
            str(tmp_path / "gold.db"),
            "--min-referrers",
            "3",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert captured[0].min_referrers == 3
