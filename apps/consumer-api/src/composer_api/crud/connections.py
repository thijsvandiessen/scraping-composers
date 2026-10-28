"""One entity's neighbours in the gold graph, budgeted so a page can draw them.

Served from the snapshot ``composer-ingest export-neo4j`` stores in gold
(:class:`composer_models.EntityConnections`, built by
:mod:`composer_graph.connections`, which also explains how each edge is found
and scored), so the API needs no graph server. Gold itself answers whether
the entity exists, so an unknown id is a 404 whether or not a snapshot was
taken.

Every knob here exists because the raw neighbourhood is unusable. A busy
conductor touches hundreds of composers and thousands of colleagues, and the
heaviest edges are the least interesting ones — everybody performs Beethoven,
so "performs Beethoven" says nothing about anybody. Four budgets cut that down
to something a reader (and a layout) can take in:

``min_weight``
    Drops one-off co-occurrences, which are mostly matcher noise.
``per_relation``
    A separate budget per relation, so the performance edges — always the
    numerous ones — cannot crowd the handful of biographical edges out of the
    graph entirely. Left unset it is a reservation rather than a cap: each
    relation is guaranteed its share, and whatever ``limit`` has left over goes
    to the next-best edges of any relation.
``limit``
    The overall cap, sized for a drawing rather than for completeness.
``rank``
    ``weight`` ranks by raw co-occurrence and surfaces the famous. ``affinity``
    (the default) discounts each neighbour by its overall reach, which demotes
    the hubs everyone connects to and lets a distinctive pairing come out on
    top. ``weight`` is always there to check it against.

The snapshot keeps the best ``POOL_SIZE`` edges per relation under each
ranking, so a high ``min_weight`` filters that pool rather than reaching past
it, and can return fewer than ``limit`` edges where more exist; ``total`` is
counted from all of them and stays exact.
"""

import uuid
from collections import Counter
from typing import cast

from composer_models import CLAIMS, ConnectionEdge, ConnectionsPayload, Entity, EntityConnections
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..deps import GraphBudget
from ..errors import GraphUnavailableError, NotFoundError
from ..schemas import ConnectionOut, ConnectionsOut

# The per-relation allowance when the caller sets none: a reservation, see
# ``_budgeted``. Six of each keeps the default 24-node drawing varied.
RESERVED_PER_RELATION = 6

PERFORMANCE_FAMILIES = ("performed", "performed_by", "appeared_with")


def _selected(edges: list[ConnectionEdge], budget: GraphBudget) -> list[ConnectionOut]:
    """The edges ``relation`` and ``min_weight`` let through, best first, each
    named by the relation it was selected for."""
    chosen: list[ConnectionOut] = []
    for edge in edges:
        if edge["weight"] < budget.min_weight:
            continue
        if budget.relation is None:
            relation = edge["relations"][0]
        elif budget.relation in PERFORMANCE_FAMILIES:
            if edge["family"] != budget.relation:
                continue
            relation = budget.relation
        elif edge["family"] == CLAIMS and budget.relation in edge["relations"]:
            relation = budget.relation
        else:
            continue
        chosen.append(
            ConnectionOut(
                entity_id=uuid.UUID(edge["entity_id"]),
                label=edge["label"],
                kind=edge["kind"],
                relation=relation,
                relations=edge["relations"],
                direction=edge["directions"][relation],
                weight=edge["weight"],
                score=round(edge["scores"][budget.rank], 4),
                roles=edge["roles"],
                via=edge["via"],
            )
        )
    return chosen


def _total(weights: dict[str, dict[str, int]], budget: GraphBudget) -> int:
    """How many edges cleared ``min_weight`` before the budgets."""
    if budget.relation is None:
        families = [*PERFORMANCE_FAMILIES, CLAIMS]
    else:
        families = [budget.relation]
    return sum(
        n
        for family in families
        for weight, n in weights.get(family, {}).items()
        if int(weight) >= budget.min_weight
    )


def _budgeted(items: list[ConnectionOut], per_relation: int | None, limit: int) -> list[ConnectionOut]:
    """Top ``per_relation`` of each relation, then the best ``limit`` overall.

    ``items`` arrives sorted best-first, so one pass gives each relation its own
    budget: a conductor's thousand performance edges cannot squeeze out the two
    claims that say where he was born, because they are spending different
    allowances. With ``per_relation`` unset, a second pass hands whatever
    ``limit`` has left to the next-best edges, so a page asking for 48 nodes
    gets 48 even when only two relations are present.
    """
    allowance = per_relation or RESERVED_PER_RELATION
    taken: Counter[str] = Counter()
    kept: set[int] = set()
    for index, item in enumerate(items):
        if len(kept) >= limit:
            break
        if taken[item.relation] < allowance:
            taken[item.relation] += 1
            kept.add(index)
    if per_relation is None:
        for index in range(len(items)):
            if len(kept) >= limit:
                break
            kept.add(index)
    return [item for index, item in enumerate(items) if index in kept]


def entity_connections(
    db: Session, entity_id: uuid.UUID, budget: GraphBudget | None = None
) -> ConnectionsOut:
    """The strongest neighbours of one entity, ready to draw.

    ``total`` reports how many edges cleared ``min_weight`` before the budgets
    were applied, so a caller can tell a sparse neighbourhood from a truncated
    one.
    """
    budget = budget or GraphBudget()
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise NotFoundError("entity not found")
    snapshot = db.get(EntityConnections, entity_id)
    if snapshot is None and not db.scalar(select(exists().select_from(EntityConnections))):
        raise GraphUnavailableError("no connections snapshot: run `composer-ingest export-neo4j`")

    total, kept = 0, list[ConnectionOut]()
    # No row for an entity with no neighbours, or no node at all (a work).
    if snapshot is not None:
        payload = cast(ConnectionsPayload, snapshot.payload)
        # Ties broken on the id so a page reloads to the same picture.
        edges = sorted(
            payload["edges"], key=lambda e: (-e["scores"][budget.rank], -e["weight"], e["entity_id"])
        )
        total = _total(payload["weights"], budget)
        kept = _budgeted(_selected(edges, budget), budget.per_relation, budget.limit)

    return ConnectionsOut(
        entity_id=entity.id,
        label=entity.label,
        kind=entity.kind,
        items=kept,
        total=total,
        limit=budget.limit,
        rank=budget.rank,
    )
