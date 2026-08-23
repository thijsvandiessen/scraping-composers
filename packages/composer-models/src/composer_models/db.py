"""Engine/session setup for the silver database.

Defaults to a local SQLite file. Set ``DATABASE_URL`` to a
``postgresql+psycopg://user:pass@host:5432/composers`` URL to use Postgres
instead (install with the ``postgres`` extra); ``SILVER_SCHEMA`` names the
schema it lives in.
"""

from __future__ import annotations

import re

from composer_config import settings
from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from . import Base

# Schema names cannot be bound parameters, so anything that reaches DDL has to
# be validated rather than escaped.
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,50}$")


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
        connect_args={
            "options": f"-csearch_path={name}",
            # psycopg auto-prepares repeated statements, and a prepared plan
            # pins the relation OID. A schema rename doesn't change OIDs, so a
            # pooled reader would keep reading the *demoted* tables after a
            # rebuild swap and then fail outright once they are dropped. With
            # auto-prepare off, every execution re-resolves by name and follows
            # the swap — which is how the read APIs survive a rebuild without a
            # restart.
            "prepare_threshold": None,
        },
        pool_pre_ping=True,
        pool_recycle=300,
    )


def init_db(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
