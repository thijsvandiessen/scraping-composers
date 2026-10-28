"""Each entity's neighbourhood in the gold graph, snapshotted as JSON.

``/v1/entities/{id}/connections`` used to ask Neo4j on every request, which
put a graph server in production for the sake of one endpoint. Instead
``composer-ingest export-neo4j`` (see :mod:`composer_graph.connections`) asks
it once per entity after loading the graph and writes the answer here, and
the API budgets that stored answer per request. Gold is rebuilt by every
promote, so this table is empty until the graph is exported again.

The payload is a candidate pool rather than a finished response, so the
API's ``rank``, ``limit``, ``per_relation``, ``relation`` and ``min_weight``
still work: the best ``POOL_SIZE`` edges per relation under either ranking
(100 is the API's largest ``limit``), each scored both ways, plus a count of
every edge by weight so ``total`` stays exact whatever ``min_weight`` is.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy import JSON, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

# Edges kept per relation and ranking; the API's ``limit`` maximum.
POOL_SIZE = 100

# The family of the claim edges in ``ConnectionEdge.family``; the performance
# families are named after their relation.
CLAIMS = "claims"


class ConnectionEdge(TypedDict):
    """One neighbour, as the API serves it, before the per-request budgets."""

    family: str  # performed | performed_by | appeared_with | claims
    entity_id: str
    label: str
    kind: str
    relations: list[str]  # every relation linking the two, strongest first
    directions: dict[str, str]  # relation -> out | in
    weight: int
    scores: dict[str, float]  # rank (affinity | weight) -> score
    roles: list[str]
    via: list[str]


class ConnectionsPayload(TypedDict):
    edges: list[ConnectionEdge]
    # family or claim predicate -> {weight: edges of that weight}; JSON keys are strings
    weights: dict[str, dict[str, int]]


class EntityConnections(Base):
    """The neighbourhood snapshot of one graph node; see the module docstring."""

    __tablename__ = "entity_connections"

    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("entities.id"), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
