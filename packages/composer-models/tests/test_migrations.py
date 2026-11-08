"""The migration tree, and the guarantee that it still matches the models.

SQLite silver is created by ``create_all`` and rebuilt from bronze, so most of
these only run against Postgres — the backend where an existing database has to
be migrated rather than replayed. The single-head check needs no database.
"""

from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from composer_models import Base
from composer_models.alembic_support import alembic_config
from composer_models.db import get_engine
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from sqlalchemy import inspect


@requires_postgres
def test_upgrade_head_creates_the_schema_from_nothing(pg_url: str) -> None:
    """`alembic upgrade head` is the whole bootstrap for an empty database —
    it creates the silver schema itself, not just the tables inside it."""
    command.upgrade(alembic_config(pg_url), "head")

    engine = get_engine(pg_url)
    try:
        assert set(inspect(engine).get_table_names()) >= set(Base.metadata.tables)
    finally:
        engine.dispose()


@requires_postgres
def test_head_matches_the_models(pg_url: str) -> None:
    """The tree at head produces exactly what ``create_all`` would.

    This is what keeps "migrations are Postgres-only" honest: add a column to a
    model without writing a revision and this fails. Type comparison is off —
    Float() renders FLOAT but reflects back as DOUBLE PRECISION, which is a
    spurious diff; structure (tables, columns, nullability, indexes,
    constraints) is the property that matters.
    """
    command.upgrade(alembic_config(pg_url), "head")

    engine = get_engine(pg_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": False})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_the_tree_has_a_single_head() -> None:
    """Two PRs that each add a revision on the same parent pass CI apart and
    leave ``main`` with two heads, where ``upgrade head`` refuses to run. This
    needs no database, so the plain test run catches it, not only Postgres CI."""
    heads = ScriptDirectory.from_config(alembic_config("sqlite://")).get_heads()

    assert len(heads) == 1, f"merge the heads with `alembic merge`: {heads}"
