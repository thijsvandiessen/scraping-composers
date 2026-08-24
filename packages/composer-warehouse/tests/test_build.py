"""Tests for the shared atomic-swap build helper."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from composer_warehouse.build import (
    SqliteFileTarget,
    read_build_manifest,
    run_build,
)
from sqlalchemy import Engine, text


@dataclass(frozen=True)
class _Stats:
    rows: int = 0


def _write(engine: Engine, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS marker (value TEXT)"))
        conn.execute(text("INSERT INTO marker (value) VALUES (:value)"), {"value": value})


def _read(db_path: Path) -> str | None:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT value FROM marker")).scalar()
    finally:
        engine.dispose()


def test_run_build_swaps_result_in_and_writes_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "out.db"

    def build(engine: Engine) -> _Stats:
        _write(engine, "built")
        return _Stats(rows=3)

    stats = run_build(SqliteFileTarget(db_path), build)

    assert stats == _Stats(rows=3)
    assert _read(db_path) == "built"
    assert not Path(f"{db_path}.tmp").exists()
    manifest = read_build_manifest(db_path)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.finished_at is not None
    assert manifest.stats == {"rows": 3}


def test_run_build_failure_keeps_previous_database(tmp_path: Path) -> None:
    db_path = tmp_path / "out.db"

    def seed(engine: Engine) -> _Stats:
        _write(engine, "previous")
        return _Stats(rows=1)

    run_build(SqliteFileTarget(db_path), seed)

    def build(engine: Engine) -> _Stats:
        _write(engine, "half-built")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_build(SqliteFileTarget(db_path), build)

    assert _read(db_path) == "previous"
    manifest = read_build_manifest(db_path)
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.error == "RuntimeError: boom"


def test_run_build_failure_leaves_no_staging_file(tmp_path: Path) -> None:
    """A crashed build must not orphan a half-built database beside the real
    one — the next run would otherwise start from someone else's leftovers."""
    db_path = tmp_path / "out.db"

    def build(engine: Engine) -> _Stats:
        _write(engine, "half-built")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_build(SqliteFileTarget(db_path), build)

    assert not Path(f"{db_path}.tmp").exists()


def test_run_build_discards_the_staging_area_on_keyboard_interrupt(tmp_path: Path) -> None:
    # KeyboardInterrupt is a BaseException: an interrupted hour-long rebuild
    # has to clean up just like a failed one.
    db_path = tmp_path / "out.db"

    def build(engine: Engine) -> _Stats:
        _write(engine, "half-built")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_build(SqliteFileTarget(db_path), build)

    assert not Path(f"{db_path}.tmp").exists()
    manifest = read_build_manifest(db_path)
    assert manifest is not None
    assert manifest.status == "failed"


def test_read_build_manifest_missing_returns_none(tmp_path: Path) -> None:
    assert read_build_manifest(tmp_path / "nowhere.db") is None
