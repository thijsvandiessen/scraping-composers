import hmac
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

from composer_models.db import get_engine, init_db
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _get_session_factory() -> sessionmaker[Session]:
    global _engine, _session_factory
    if _session_factory is None:
        _engine = get_engine()
        _session_factory = init_db(_engine)
    return _session_factory


def dispose_db() -> None:
    """Drop the cached engine and session factory.

    After ``rebuild-silver`` atomically swaps the database file, pooled
    connections still point at the old inode; disposing forces the next
    request to re-open the new file.
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


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
    """Guard the admin surface with the shared secret in ``ADMIN_API_KEY``.

    Fails closed: when the env var is unset the API refuses every request with
    a 503, so a deployment that forgets to configure the key is unusable rather
    than wide open. Local development sets the key explicitly (see README).
    """
    from composer_config import settings

    expected = settings.admin_api_key
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "admin API not configured: set ADMIN_API_KEY",
        )
    if x_admin_key is None or not hmac.compare_digest(x_admin_key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-Admin-Key")
