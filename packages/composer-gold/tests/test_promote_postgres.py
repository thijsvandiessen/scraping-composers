"""Promote into Postgres: the schema swap, its manifest, and what readers see.

Silver stays the in-memory SQLite fixture except where the silver lock is the
point; ``pg_url`` points ``GOLD_DATABASE_URL`` at the test server and gives
this test a gold schema of its own.
"""

from __future__ import annotations

import pytest
from composer_config import settings
from composer_gold import gold_engine, promote, read_gold_manifest
from composer_models import Source
from composer_models.db import get_engine
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from composer_warehouse.rebuild import silver_target
from composer_warehouse.testing import FakeSource, ingest_source, mention
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_promote import _seed_silver  # pyright: ignore[reportImplicitRelativeImport]

pytestmark = requires_postgres


def _schemas_like(url: str, pattern: str) -> list[str]:
    engine = get_engine(url, schema="public")
    try:
        with engine.connect() as conn:
            return list(
                conn.scalars(
                    text("SELECT nspname FROM pg_namespace WHERE nspname LIKE :pattern ORDER BY 1"),
                    {"pattern": pattern},
                )
            )
    finally:
        engine.dispose()


def test_promote_swaps_the_gold_schema_in_and_records_a_manifest(session: Session, pg_url: str) -> None:
    _seed_silver(session)
    first = promote(session)
    second = promote(session)  # the swap path: a live gold schema already exists

    assert first == second
    # No staging or demoted schema is left behind, only the live one.
    assert _schemas_like(pg_url, f"{settings.gold_schema}%") == [settings.gold_schema]
    manifest = read_gold_manifest()
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.stats["persons_kept"] == 2

    engine = gold_engine()
    try:
        with engine.connect() as conn:
            # Stamped like rebuild-silver stamps, so it reads as migrated.
            assert conn.scalar(text("SELECT count(*) FROM alembic_version")) == 1
    finally:
        engine.dispose()


def test_gold_readers_follow_the_swap(session: Session, pg_url: str) -> None:
    _seed_silver(session)
    promote(session)
    engine = gold_engine()
    try:
        with Session(engine) as gold:
            assert gold.scalar(select(func.count(Source.id))) == 2

        more = FakeSource(
            records=(mention("Das Lied von der Erde", "Mahler, Gustav", "x1"),),
            name="extra",
            base_url="https://extra.example",
        )
        ingest_source(session, more)
        promote(session)

        # The same engine, and so the same pooled connection, sees the new gold.
        with Session(engine) as gold:
            assert gold.scalar(select(func.count(Source.id))) == 3
    finally:
        engine.dispose()


def test_promoted_ids_leave_sequences_usable(session: Session, pg_url: str) -> None:
    _seed_silver(session)
    promote(session)
    engine = gold_engine()
    try:
        with Session(engine) as gold:
            # Sources are copied with silver's ids; without the resync the
            # sequence would hand out 1 again and collide.
            gold.add(Source(name="later", base_url="https://later.example"))
            gold.commit()
            assert gold.scalar(select(func.count(Source.id))) == 3
    finally:
        engine.dispose()


def test_promote_refuses_to_read_silver_mid_rebuild(pg_url: str) -> None:
    rebuild = silver_target(pg_url)
    rebuild.begin()  # holds silver's rebuild lock, as a running rebuild would
    silver = get_engine(pg_url)
    try:
        with Session(silver) as session, pytest.raises(RuntimeError, match="being rebuilt"):
            promote(session)
    finally:
        rebuild.abort()
        silver.dispose()
    # Refused before the build started: no manifest claims a promote ran.
    assert read_gold_manifest() is None
