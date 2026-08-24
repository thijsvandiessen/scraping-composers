"""The Postgres schema swap: the atomic-rename analogue of the file replace."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from composer_config import settings
from composer_models.db import get_engine
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from composer_warehouse.build import run_build
from composer_warehouse.rebuild import silver_target
from sqlalchemy import Engine, text

pytestmark = requires_postgres


@dataclass(frozen=True)
class _Stats:
    rows: int = 0


def _write(engine: Engine, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE marker (value TEXT)"))
        conn.execute(text("INSERT INTO marker (value) VALUES (:value)"), {"value": value})


def _live_marker(url: str) -> str | None:
    engine = get_engine(url)
    try:
        with engine.connect() as conn:
            return conn.scalar(text("SELECT value FROM marker"))
    finally:
        engine.dispose()


def _schemas_like(url: str, pattern: str) -> list[str]:
    engine = get_engine(url, schema="public")
    try:
        with engine.connect() as conn:
            return list(
                conn.scalars(
                    text("SELECT nspname FROM pg_namespace WHERE nspname LIKE :pattern"),
                    {"pattern": pattern},
                )
            )
    finally:
        engine.dispose()


def _build_writing(value: str):
    def build(engine: Engine) -> _Stats:
        _write(engine, value)
        return _Stats(rows=1)

    return build


def test_first_build_on_an_empty_database(pg_url: str) -> None:
    # Nothing to demote yet: commit() must not try to rename a schema that
    # isn't there.
    stats = run_build(silver_target(pg_url), _build_writing("first"))

    assert stats == _Stats(rows=1)
    assert _live_marker(pg_url) == "first"


def test_swap_replaces_the_live_schema(pg_url: str) -> None:
    run_build(silver_target(pg_url), _build_writing("old"))
    run_build(silver_target(pg_url), _build_writing("new"))

    assert _live_marker(pg_url) == "new"
    # The demoted copy is cleaned up rather than accumulating.
    assert _schemas_like(pg_url, f"{settings.silver_schema}\\_old") == []


def test_failed_build_leaves_the_live_schema_untouched(pg_url: str) -> None:
    run_build(silver_target(pg_url), _build_writing("good"))

    def failing(engine: Engine) -> _Stats:
        _write(engine, "half-built")
        raise RuntimeError("boom")

    target = silver_target(pg_url)
    with pytest.raises(RuntimeError, match="boom"):
        run_build(target, failing)

    assert _live_marker(pg_url) == "good"
    manifest = target.read_manifest()
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.error == "RuntimeError: boom"
    # And no staging schema is orphaned behind it.
    assert _schemas_like(pg_url, f"{settings.silver_schema}\\_build\\_%") == []


def test_readers_survive_the_swap(pg_url: str) -> None:
    """A pooled reader sees the new data after a rebuild, without a restart.

    Name resolution happens per statement and Postgres invalidates the cached
    plans a rename affects, so a connection that predates the swap follows it —
    including one whose statements psycopg has prepared server-side (verified
    both with and without auto-prepare). This is the property the ``NullPool``
    branch in consumer-api provides for SQLite, where the swap replaces the
    file under an already-open handle; Postgres needs no such workaround.
    """
    run_build(silver_target(pg_url), _build_writing("before"))

    reader = get_engine(pg_url)
    try:
        with reader.connect() as conn:
            # Well past psycopg's auto-prepare threshold, so the reader is
            # in the state most likely to have cached a stale plan.
            for _ in range(10):
                assert conn.scalar(text("SELECT value FROM marker")) == "before"

            run_build(silver_target(pg_url), _build_writing("after"))

            conn.rollback()  # end the snapshot the reader has been holding
            assert conn.scalar(text("SELECT value FROM marker")) == "after"
    finally:
        reader.dispose()


def test_manifest_survives_the_schema_it_describes(pg_url: str) -> None:
    # The manifest lives in composer_meta, outside everything the swap touches,
    # so it cannot be destroyed by the build it is recording.
    run_build(silver_target(pg_url), _build_writing("one"))
    run_build(silver_target(pg_url), _build_writing("two"))

    manifest = silver_target(pg_url).read_manifest()
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.stats == {"rows": 1}
    assert manifest.finished_at is not None


def test_concurrent_rebuild_is_rejected(pg_url: str) -> None:
    first = silver_target(pg_url)
    first.begin()
    try:
        with pytest.raises(RuntimeError, match="already in progress"):
            silver_target(pg_url).begin()
    finally:
        first.abort()

    # Once the first build releases the lock, a new one can start.
    second = silver_target(pg_url)
    second.begin()
    second.abort()


def test_exists_reports_a_built_database_not_a_bare_schema(pg_url: str) -> None:
    target = silver_target(pg_url)
    assert target.exists() is False

    run_build(silver_target(pg_url), lambda engine: _Stats(rows=0))
    # The schema exists now but holds no silver tables, which is not a
    # database anyone can read.
    assert silver_target(pg_url).exists() is False
