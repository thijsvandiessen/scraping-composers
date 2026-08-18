"""Engine/session setup. Set DATABASE_URL to switch to Postgres, e.g.
``postgresql+psycopg://user:pass@host:5432/composers`` (install with the
``postgres`` extra). Defaults to a local SQLite file."""

from __future__ import annotations

from composer_config import settings
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from . import Base


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or settings.database_url)


def init_db(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
