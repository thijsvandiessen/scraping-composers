"""Tests for CLI query helpers (claim provenance lookup) and CLI commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from composer_ingest.cli import cmd_claims, cmd_ingest, cmd_runs, cmd_stats, entity_claims, main
from composer_ingest.db import get_engine, init_db
from composer_ingest.ingest import run_ingest
from composer_ingest.sources import SourceClaim
from test_ingest import FakeSource, person


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
        NAME="concertgebouw",
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
        NAME="wikidata",
    )
    run_ingest(session, a)
    run_ingest(session, b)


def test_entity_claims_attributes_each_value_to_its_source(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((entity, rows),) = entity_claims(session, "Abert, Johann Joseph")
    assert entity.label == "Abert, Johann Joseph"  # deduped to one entity

    born = [(value, source) for predicate, value, _obj, source, _rec in rows if predicate == "born_on"]
    assert born == [("1832", "concertgebouw"), ("1832-09-20", "wikidata")]


def test_entity_claims_filters_by_predicate_and_source(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((_, rows),) = entity_claims(session, "Abert", predicate="born_on", source="wikidata")
    assert [(r[0], r[1], r[3]) for r in rows] == [("born_on", "1832-09-20", "wikidata")]


def test_entity_claims_carries_record_provenance(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((_, rows),) = entity_claims(session, "Abert", predicate="born_on")
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
        NAME="wikidata",
    )
    run_ingest(session, source)

    ((_, rows),) = entity_claims(session, "Bach, Johann Sebastian", predicate="has_profession")
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
        run_ingest(
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
# cmd_ingest
# ---------------------------------------------------------------------------


def test_cmd_ingest_returns_0_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_ingest.sources import REGISTRY

    db_url = f"sqlite:///{tmp_path}/test.db"
    fake = FakeSource(records=(person("Bach, Johann Sebastian"),), NAME="fake")
    monkeypatch.setitem(REGISTRY, "fake", fake)

    rc = cmd_ingest(_ns(database_url=db_url, source="fake", max_pages=None))
    assert rc == 0


def test_cmd_ingest_returns_1_on_source_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_ingest.sources import REGISTRY

    db_url = f"sqlite:///{tmp_path}/test.db"
    fake = FakeSource(records=(person("Mozart"), person("Haydn")), NAME="fake", fail_after=1)
    monkeypatch.setitem(REGISTRY, "fake", fake)

    rc = cmd_ingest(_ns(database_url=db_url, source="fake", max_pages=None))
    assert rc == 1


# ---------------------------------------------------------------------------
# cmd_claims
# ---------------------------------------------------------------------------


def test_cmd_claims_prints_entity_and_claims(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_url = f"sqlite:///{tmp_path}/test.db"
    factory = init_db(get_engine(db_url))
    with factory() as session:
        run_ingest(
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
        run_ingest(session, FakeSource(records=(person("Haydn, Joseph"),), NAME="wikidata"))

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
        run_ingest(session, FakeSource(records=(person("Bach, J.S.", SourceClaim("born_on", value="1685")),)))

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
        run_ingest(session, FakeSource(records=(person("Beethoven"),), NAME="wikidata"))

    monkeypatch.setattr(sys, "argv", ["composer-ingest", "--database-url", db_url, "runs"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "wikidata" in capsys.readouterr().out


def test_get_engine_reads_database_url_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{tmp_path}/env.db"
    monkeypatch.setenv("DATABASE_URL", db_url)
    engine = get_engine()  # no explicit URL — falls back to env var
    assert str(engine.url) == db_url
