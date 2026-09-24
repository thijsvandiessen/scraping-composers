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


@dataclass(frozen=True)
class Filters:
    """The shared ``q``/``source`` query parameters of the list endpoints.

    Grouped the way ``Pagination`` groups ``page``/``limit``: every list
    endpoint accepts both, and the crud functions stay under the argument cap.
    """

    q: str | None = None
    source: str | None = None


FilterQuery = Annotated[Filters, Depends()]


@dataclass(frozen=True)
class GraphBudget:
    """The caps that turn a raw neighbourhood into a drawable one.

    Grouped for the same reason as ``Pagination`` and ``Filters``: the whole
    set travels together on every connections request, and passing them
    individually puts both the route and its crud function over the argument
    cap. See ``crud.connections`` for what each one is for.
    """

    limit: Annotated[int, Query(ge=1, le=100)] = 24
    per_relation: Annotated[int, Query(ge=1, le=50)] = 6
    min_weight: Annotated[int, Query(ge=1)] = 1
    rank: Annotated[str, Query(pattern="^(affinity|weight)$")] = "affinity"
    relation: str | None = None


GraphQuery = Annotated[GraphBudget, Depends()]
