"""Engine/session setup for the silver database.

Defaults to a local SQLite file. Set ``DATABASE_URL`` to a
``postgresql+psycopg://user:pass@host:5432/composers`` URL to use Postgres
instead (install with the ``postgres`` extra); ``SILVER_SCHEMA`` names the
schema it lives in.
"""

from __future__ import annotations

import re

from composer_config import settings
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from . import Base

# Schema names cannot be bound parameters, so anything that reaches DDL has to
# be validated rather than escaped.
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,50}$")
# Table and column names reach SQL as text too, for the same reason.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def get_engine(url: str | None = None, *, schema: str | None = None) -> Engine:
    """Engine for ``url`` (default ``DATABASE_URL``).

    On Postgres the connection is pinned to the silver schema through libpq's
    ``-csearch_path``, so unqualified DDL and ORM statements can only ever
    touch that schema. That is what lets ``rebuild-silver`` build into a
    staging schema with the same models and the same code: the pin is enforced
    server-side, so nothing compiled can escape it, and a missing schema fails
    loudly instead of silently writing to the live one.
    """
    resolved = make_url(url or settings.database_url)
    if resolved.get_backend_name() != "postgresql":
        return create_engine(resolved)
    name = schema or settings.silver_schema
    if not _SCHEMA_NAME.match(name):
        raise ValueError(f"invalid schema name {name!r}")
    return create_engine(
        resolved,
        connect_args={"options": f"-csearch_path={name}"},
        # A rebuild renames schemas under live readers; pre-ping so a pooled
        # connection that was broken meanwhile is replaced rather than raised.
        pool_pre_ping=True,
        pool_recycle=300,
    )


def init_db(engine: Engine) -> sessionmaker[Session]:
    """Session factory for ``engine``, creating the schema on SQLite.

    On SQLite the schema is created on demand: silver is disposable there, and
    a schema change means replaying bronze rather than migrating. On Postgres
    the schema belongs to Alembic (``alembic upgrade head``) or to
    ``rebuild-silver``'s staging build, both of which stamp what they create —
    so creating tables here would leave an unstamped schema that no migration
    could ever run against.
    """
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def resync_pk_sequence(session: Session, table: str, column: str = "id") -> None:
    """Move a serial primary key's sequence past the largest id in the table.

    Bulk inserts that assign integer ids explicitly (the concert and recording
    derivations number their rows) bypass the sequence, which on Postgres stays
    at 1 — so the next ORM insert collides on the primary key. SQLite has no
    sequences and no such problem, so this is a no-op there.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    if not _IDENTIFIER.match(table) or not _IDENTIFIER.match(column):
        raise ValueError(f"invalid table or column name: {table!r}.{column!r}")
    session.execute(
        text(
            "SELECT setval(pg_get_serial_sequence(:table, :column),"
            f" coalesce((SELECT max({column}) FROM {table}), 0) + 1, false)"
        ),
        {"table": table, "column": column},
    )
