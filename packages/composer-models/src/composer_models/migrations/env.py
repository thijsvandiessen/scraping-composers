"""Alembic environment for the silver schema.

The URL is never configured in ``alembic.ini``: it comes from
``composer_config.settings`` through :func:`composer_models.db.get_engine`, so
``alembic upgrade head`` always targets whatever the application targets.

Note what is deliberately *absent*: ``version_table_schema``. ``get_engine``
pins ``search_path`` to the silver schema on Postgres, which makes
``current_schema()`` the silver schema — so ``alembic_version`` is created
inside it, reflection targets it, and autogenerate never writes a
``schema=`` argument into a revision. The revisions stay schema-agnostic,
which is also what lets them run unchanged against SQLite.
"""

from __future__ import annotations

from alembic import context
from composer_config import settings
from composer_models import Base
from composer_models.db import get_engine
from sqlalchemy import Connection, text

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (``alembic upgrade --sql``)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A caller running migrations in-process (tests, the rebuild's stamp) hands
    # us its own connection so the work joins its transaction.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return

    engine = get_engine()
    if engine.dialect.name == "postgresql":
        # The pinned search_path names a schema that may not exist yet; create
        # it so `alembic upgrade head` is the whole bootstrap for a fresh
        # database. Unqualified DDL below then lands inside it.
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.silver_schema}"'))
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
