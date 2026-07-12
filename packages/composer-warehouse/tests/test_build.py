"""Tests for the shared atomic-swap build helper."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from composer_warehouse.build import read_build_manifest, run_build


@dataclass(frozen=True)
class _Stats:
    rows: int = 0


def test_run_build_swaps_result_in_and_writes_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "out.db"

    def build(tmp: Path) -> _Stats:
        tmp.write_text("built")
        return _Stats(rows=3)

    stats = run_build(db_path, build)

    assert stats == _Stats(rows=3)
    assert db_path.read_text() == "built"
    assert not Path(f"{db_path}.tmp").exists()
    manifest = read_build_manifest(db_path)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.finished_at is not None
    assert manifest.stats == {"rows": 3}


def test_run_build_failure_keeps_previous_database(tmp_path: Path) -> None:
    db_path = tmp_path / "out.db"
    db_path.write_text("previous")

    def build(tmp: Path) -> _Stats:
        tmp.write_text("half-built")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_build(db_path, build)

    assert db_path.read_text() == "previous"
    manifest = read_build_manifest(db_path)
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.error == "RuntimeError: boom"


def test_read_build_manifest_missing_returns_none(tmp_path: Path) -> None:
    assert read_build_manifest(tmp_path / "nowhere.db") is None
