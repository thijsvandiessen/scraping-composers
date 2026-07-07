from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session


def get_db() -> Generator[Session, None, None]:
    """Placeholder dependency; every app binds its own database via
    ``create_app`` (see ``main.py``), which overrides this."""
    raise RuntimeError("get_db must be overridden by the app; build apps via create_app")
    yield  # pragma: no cover  # makes this a generator, matching the override's shape


DbSession = Annotated[Session, Depends(get_db)]
