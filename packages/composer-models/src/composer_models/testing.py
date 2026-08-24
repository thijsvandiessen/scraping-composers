"""Fixtures for tests that need a real Postgres.

Everything here is inert unless ``COMPOSER_TEST_POSTGRES_URL`` names a database
the test run may rewrite (``docker compose up -d postgres``). With it unset,
``requires_postgres`` skips, so the SQLite-only test matrix and a developer
without Docker both pass untouched.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from composer_config import settings
from sqlalchemy import text

from .db import get_engine

POSTGRES_URL_ENV = "COMPOSER_TEST_POSTGRES_URL"


def postgres_test_url() -> str | None:
    return os.environ.get(POSTGRES_URL_ENV) or None


requires_postgres = pytest.mark.skipif(
    postgres_test_url() is None,
    reason=f"set {POSTGRES_URL_ENV} to a scratch database to run the Postgres tests",
)


def drop_test_schemas(url: str, prefix: str) -> None:
    """Drop ``prefix``, everything a rebuild derived from it, and its manifest.

    The build manifest deliberately lives outside the schemas a swap replaces,
    so dropping those does not reach it — a test's row would otherwise outlive
    the test and accumulate in a shared database.
    """
    engine = get_engine(url, schema="public")
    try:
        with engine.begin() as conn:
            names = conn.scalars(
                text("SELECT nspname FROM pg_namespace WHERE nspname = :prefix OR nspname LIKE :pattern"),
                {"prefix": prefix, "pattern": f"{prefix}\\_%"},
            ).all()
            for name in names:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
            if conn.scalar(text("SELECT to_regclass('composer_meta.build_manifest')")) is not None:
                conn.execute(
                    text("DELETE FROM composer_meta.build_manifest WHERE target = :target"),
                    {"target": prefix},
                )
    finally:
        engine.dispose()


@pytest.fixture
def pg_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A Postgres URL whose silver schema is this test's own throwaway.

    ``settings.silver_schema`` is patched, so ``get_engine()`` and every build
    target derived from settings land in a schema no other test shares — tests
    stay isolated and can run in parallel against one database.
    """
    url = postgres_test_url()
    if url is None:  # pragma: no cover - guarded by requires_postgres
        pytest.skip(f"{POSTGRES_URL_ENV} is not set")
    schema = f"t{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(settings, "silver_schema", schema)
    monkeypatch.setattr(settings, "database_url", url)
    try:
        yield url
    finally:
        drop_test_schemas(url, schema)
