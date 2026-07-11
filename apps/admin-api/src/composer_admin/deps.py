import hmac
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
