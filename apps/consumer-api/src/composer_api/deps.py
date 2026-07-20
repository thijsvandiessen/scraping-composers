from collections.abc import Generator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session


def get_db() -> Generator[Session, None, None]:
    """Placeholder dependency; every app binds its own database via
    ``create_app`` (see ``main.py``), which overrides this."""
    raise RuntimeError("get_db must be overridden by the app; build apps via create_app")
    yield  # pragma: no cover  # makes this a generator, matching the override's shape


DbSession = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Pagination:
    """The shared ``page``/``limit`` query parameters of the list endpoints."""

    page: Annotated[int, Query(ge=1)] = 1
    limit: Annotated[int, Query(ge=1, le=100)] = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


PageQuery = Annotated[Pagination, Depends()]
