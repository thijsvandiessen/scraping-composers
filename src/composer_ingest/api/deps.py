from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from ..etl.db import get_engine, init_db

_session_factory: sessionmaker[Session] | None = None


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = init_db(get_engine())
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    with _get_session_factory()() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]
