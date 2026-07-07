import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

from composer_warehouse.db import get_engine, init_db
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session, sessionmaker

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


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """A standalone session for background tasks.

    A request-scoped :data:`DbSession` is closed once the response is sent, so a
    background scrape must open its own session to keep working after that.
    """
    with _get_session_factory()() as session:
        yield session


def require_admin_key(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """Guard the admin surface with a shared secret when ``ADMIN_API_KEY`` is set.

    No-op when the env var is unset, so local development stays friction-free
    while a deployed admin environment can require the ``X-Admin-Key`` header.
    """
    expected = os.environ.get("ADMIN_API_KEY")
    if expected and x_admin_key != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-Admin-Key")
