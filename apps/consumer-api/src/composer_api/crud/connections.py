"""One entity's neighbours in the gold graph, budgeted so a page can draw them.

Read from the Neo4j projection of gold (``composer-ingest export-neo4j``,
see :mod:`composer_graph`): a performer reaches the composers it programmed
through its concerts' (or recordings') works, performers billed together reach
each other, and the claims (``born_in``, ``student_of``, ``has_genre``, ...)
hang the biographical context off both ends. Gold itself still answers whether
the entity exists, so an unknown id is a 404 whether or not the graph is up.

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
    (the default) divides by the square root of the neighbour's reach *in the
    same relation family* — how often a composer is performed at all, how many
    performances a performer gives, how many events a stage partner appears at
    — which demotes the hubs everyone connects to and lets a pairing that is
    *distinctive* come out on top. ``weight`` is always there to check it
    against. The reach is stored on each node by the loader, one property per
    family; comparing an edge with a reach from another family is the mistake
    this module was rewritten to stop making.

Claim edges are ranked differently, because their weight (how many sources
assert the claim) is almost always one or two. A place, genre or movement is
discounted by how many entities it is linked to — Vienna says less about a
person than a small town does. A person or ensemble is the other way round:
on Vienna's page the people born there are ranked by how widely they are
performed, so Beethoven and Brahms come before a name nobody has heard. A
neighbour linked by several claims (born *and* died there) is one node, with
all its relations listed.
"""

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, LiteralString, cast

from composer_graph import CLAIM_TYPES, ENTITY_LABELS, claim_pattern
from composer_models import Entity
from neo4j import Driver
from sqlalchemy.orm import Session

from ..deps import GraphBudget
from ..errors import GraphUnavailableError, NotFoundError
from ..schemas import ConnectionOut, ConnectionsOut

# Relations, in the order the graph should read them.
PERFORMED = "performed"  # this entity performed that composer's music
PERFORMED_BY = "performed_by"  # that performer played this composer's music
APPEARED_WITH = "appeared_with"  # shared a concert or a recording session

# The per-relation allowance when the caller sets none: a reservation, see
# ``_budgeted``. Six of each keeps the default 24-node drawing varied.
RESERVED_PER_RELATION = 6

# Proof snippets carried per edge. Three is enough to show the reader what the
# edge is made of without turning the payload into a concert listing.
VIA_CAP = 3

_KINDS = {label: kind for kind, label in ENTITY_LABELS.items()}
_PREDICATES = {rel_type: predicate for predicate, rel_type in CLAIM_TYPES.items()}

# Shared tail of the performance queries: score each neighbour, rank, and
# return the best ``$keep`` alongside how many cleared ``min_weight``. The
# reach property is the neighbour's total in this family; below 1 it is
# clamped so a neighbour the loader didn't annotate ranks by its raw weight.
_RANKED: LiteralString = """
WITH n, weight, roles, toFloat(coalesce(n[$reach], 1)) AS reach
WHERE weight >= $min_weight
WITH n, weight, roles,
     CASE $rank WHEN 'weight' THEN toFloat(weight)
                ELSE weight / sqrt(CASE WHEN reach < 1 THEN 1.0 ELSE reach END) END AS score
ORDER BY score DESC, weight DESC, n.id
WITH collect({node: elementId(n), id: n.id, label: n.label, kind: labels(n)[0],
              weight: weight, score: score, roles: roles}) AS edges
RETURN size(edges) AS total, edges[0..$keep] AS edges
"""

# Performances are counted per event and work (PROGRAMMED.times), not per
# credit: a soloist who also conducted the night is one performance, two roles.
# The roles come with the proof, for the kept edges only — collecting them for
# every one of Beethoven's thousands of performers doubles the query.
_PERFORMANCE_QUERIES: dict[str, tuple[LiteralString, str]] = {
    PERFORMED: (
        "MATCH (f:$FOCUS {id: $id})-[:PERFORMED_IN]->(e) WITH DISTINCT f, e "
        "MATCH (e)-[pr:PROGRAMMED]->(:Work)-[:COMPOSED_BY]->(n) WHERE n <> f "
        "WITH n, sum(pr.times) AS weight, [] AS roles",
        "times_performed",
    ),
    PERFORMED_BY: (
        "MATCH (f:$FOCUS {id: $id})<-[:COMPOSED_BY]-(:Work)<-[pr:PROGRAMMED]-(e)<-[:PERFORMED_IN]-(n) "
        "WHERE n <> f WITH DISTINCT n, pr "
        "WITH n, sum(pr.times) AS weight, [] AS roles",
        "performances",
    ),
    APPEARED_WITH: (
        "MATCH (f:$FOCUS {id: $id})-[:PERFORMED_IN]->(e)<-[pi:PERFORMED_IN]-(n) "
        "WHERE n <> f "
        "WITH n, count(DISTINCT e) AS weight, collect(DISTINCT pi.role) AS roles",
        "appearances",
    ),
}

_CLAIMS: LiteralString = """
MATCH (f:$FOCUS {id: $id})-[r:$CLAIMS]-(n)
WHERE n <> f
WITH n, r, CASE WHEN startNode(r) = f THEN 'out' ELSE 'in' END AS direction,
     coalesce(r.sources, []) AS sources
ORDER BY size(sources) DESC, type(r)
WITH n, collect({type: type(r), direction: direction}) AS claims,
     sum(size(sources)) AS weight, reduce(acc = [], s IN collect(sources) | acc + s) AS sources
WHERE weight >= $min_weight
WITH n, claims, weight, sources,
     CASE WHEN n:Person OR n:Ensemble
          THEN log(2 + coalesce(n.times_performed, 0) + coalesce(n.appearances, 0))
          ELSE 1 / sqrt(toFloat(CASE WHEN coalesce(n.claim_degree, 1) < 1 THEN 1 ELSE n.claim_degree END))
     END AS factor
WITH n, claims, weight, sources, factor,
     CASE $rank WHEN 'weight' THEN toFloat(weight) ELSE weight * factor END AS score
ORDER BY score DESC, weight DESC, factor DESC, n.id
RETURN elementId(n) AS node, n.id AS id, n.label AS label, labels(n)[0] AS kind,
       claims, weight, score, sources
"""

# The proof behind each kept performance edge, in one pass over its paths:
# the performer's roles, and the distinct works with the year they were played
# (or released), for Python to put newest first and cut to three.
_PROOF: dict[str, LiteralString] = {
    PERFORMED: "MATCH (f)-[pi:PERFORMED_IN]->(e)-[:PROGRAMMED]->(w:Work)-[:COMPOSED_BY]->(n)",
    PERFORMED_BY: "MATCH (n)-[pi:PERFORMED_IN]->(e)-[:PROGRAMMED]->(w:Work)-[:COMPOSED_BY]->(f)",
}
_PROOF_TAIL: LiteralString = """
  RETURN collect(DISTINCT pi.role) AS roles,
         collect(DISTINCT [w.title, left(coalesce(e.date, e.release_date), 4)]) AS works
}
RETURN node, roles, works
"""


@dataclass
class _Edge:
    """One neighbour, as the graph returned it, before the budgets."""

    node: str  # Neo4j element id, to ask for the edge's proof without a label
    entity_id: str
    label: str
    kind: str
    relation: str
    weight: int
    score: float
    direction: str = "out"
    relations: list[str] = field(default_factory=list[str])
    roles: list[str] = field(default_factory=list[str])
    via: list[str] = field(default_factory=list[str])


def _cypher(template: LiteralString, focus: str) -> LiteralString:
    """Labels and relationship types can't be parameters, so they are formatted
    in — the focus label from ``ENTITY_LABELS``, the types from ``CLAIM_TYPES``,
    never request data. A label is what lets the lookup use the id constraint;
    without one Neo4j scans every node."""
    return cast(LiteralString, template.replace("$FOCUS", focus).replace("$CLAIMS", claim_pattern()))


def _kind(node_label: str) -> str:
    """The gold entity kind a node label stands for (``Person`` -> ``person``)."""
    return _KINDS.get(node_label) or node_label.lower()


def _run(graph: Driver, query: LiteralString, **params: Any) -> list[dict[str, Any]]:
    records, _, _ = graph.execute_query(query, params)
    return [record.data() for record in records]


def _performance_edges(
    graph: Driver, focus: str, params: dict[str, Any], relations: Iterable[str]
) -> tuple[int, list[_Edge]]:
    total = 0
    edges: list[_Edge] = []
    for relation in relations:
        head, reach = _PERFORMANCE_QUERIES[relation]
        [row] = _run(graph, _cypher(head + _RANKED, focus), reach=reach, **params)
        total += row["total"]
        edges += [
            _Edge(
                node=e["node"],
                entity_id=e["id"],
                label=e["label"],
                kind=_kind(e["kind"]),
                relation=relation,
                weight=e["weight"],
                score=e["score"],
                direction="in" if relation == PERFORMED_BY else "out",
                relations=[relation],
                roles=sorted(set(e["roles"])),
            )
            for e in row["edges"]
        ]
    return total, edges


def _claim_edges(graph: Driver, focus: str, params: dict[str, Any], only: str | None) -> list[_Edge]:
    """One edge per neighbour, its claims collapsed; the strongest names it."""
    edges: list[_Edge] = []
    for row in _run(graph, _cypher(_CLAIMS, focus), **params):
        relations = list(dict.fromkeys(_PREDICATES[c["type"]] for c in row["claims"]))
        if only is not None and only not in relations:
            continue
        primary = only or relations[0]
        direction = next(c["direction"] for c in row["claims"] if _PREDICATES[c["type"]] == primary)
        edges.append(
            _Edge(
                node=row["node"],
                entity_id=row["id"],
                label=row["label"],
                kind=_kind(row["kind"]),
                relation=primary,
                weight=row["weight"],
                score=row["score"],
                direction=direction,
                relations=relations,
                via=list(dict.fromkeys(row["sources"]))[:VIA_CAP],
            )
        )
    return edges


def _budgeted(edges: list[_Edge], per_relation: int | None, limit: int) -> list[_Edge]:
    """Top ``per_relation`` of each relation, then the best ``limit`` overall.

    ``edges`` arrives sorted best-first, so one pass gives each relation its own
    budget: a conductor's thousand performance edges cannot squeeze out the two
    claims that say where he was born, because they are spending different
    allowances. With ``per_relation`` unset, a second pass hands whatever
    ``limit`` has left to the next-best edges, so a page asking for 48 nodes
    gets 48 even when only two relations are present.
    """
    allowance = per_relation or RESERVED_PER_RELATION
    taken: Counter[str] = Counter()
    kept: set[int] = set()
    for index, edge in enumerate(edges):
        if len(kept) >= limit:
            break
        if taken[edge.relation] < allowance:
            taken[edge.relation] += 1
            kept.add(index)
    if per_relation is None:
        for index in range(len(edges)):
            if len(kept) >= limit:
                break
            kept.add(index)
    return [edge for index, edge in enumerate(edges) if index in kept]


def _newest(works: list[list[str | None]]) -> list[str]:
    """Up to ``VIA_CAP`` "Title (year)" snippets, newest first, undated last."""
    named = sorted(((title, year) for title, year in works if title is not None), key=lambda w: w[0])
    # Stable, so equal years keep the title order; an undated "" sorts last.
    named.sort(key=lambda w: w[1] or "", reverse=True)
    snippets = [f"{title} ({year})" if year else title for title, year in named]
    return list(dict.fromkeys(snippets))[:VIA_CAP]


def _attach_proof(graph: Driver, focus: str, entity_id: str, edges: list[_Edge]) -> None:
    """Hang the roles and a few real performances off each kept performance edge."""
    for relation, pattern in _PROOF.items():
        by_node = {e.node: e for e in edges if e.relation == relation}
        if not by_node:
            continue
        query = (
            "MATCH (f:$FOCUS {id: $id}) UNWIND $nodes AS node MATCH (n) WHERE elementId(n) = node "
            "CALL (f, n) { " + pattern + _PROOF_TAIL
        )
        for row in _run(graph, _cypher(query, focus), id=entity_id, nodes=list(by_node)):
            edge = by_node[row["node"]]
            edge.roles = sorted(row["roles"])
            edge.via = _newest(row["works"])


def _neighbours(graph: Driver, focus: str, entity_id: str, budget: GraphBudget) -> tuple[int, list[_Edge]]:
    """Every edge that cleared ``min_weight``, counted, and the best of them ranked."""
    keep = min(budget.per_relation or budget.limit, budget.limit)
    params: dict[str, Any] = {
        "id": entity_id,
        "min_weight": budget.min_weight,
        "rank": budget.rank,
        "keep": keep,
    }
    families = [r for r in _PERFORMANCE_QUERIES if budget.relation in (None, r)]
    total, edges = _performance_edges(graph, focus, params, families)
    if budget.relation is None or budget.relation not in _PERFORMANCE_QUERIES:
        claims = _claim_edges(graph, focus, params, budget.relation)
        total += len(claims)
        edges += claims
    # Ties broken on the id so a page reloads to the same picture.
    edges.sort(key=lambda e: (-e.score, -e.weight, e.entity_id))
    return total, edges


def entity_connections(
    db: Session, graph: Driver | None, entity_id: uuid.UUID, budget: GraphBudget | None = None
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
    if graph is None:
        raise GraphUnavailableError("this API has no graph database")

    total, kept = 0, list[_Edge]()
    # A work entity, say, is no node in the graph and so has no neighbours.
    if (focus := ENTITY_LABELS.get(entity.kind)) is not None:
        total, edges = _neighbours(graph, focus, str(entity_id), budget)
        kept = _budgeted(edges, budget.per_relation, budget.limit)
        _attach_proof(graph, focus, str(entity_id), kept)

    return ConnectionsOut(
        entity_id=entity.id,
        label=entity.label,
        kind=entity.kind,
        items=[
            ConnectionOut(
                entity_id=uuid.UUID(edge.entity_id),
                label=edge.label,
                kind=edge.kind,
                relation=edge.relation,
                relations=edge.relations,
                direction=edge.direction,
                weight=edge.weight,
                score=round(edge.score, 4),
                roles=edge.roles,
                via=edge.via,
            )
            for edge in kept
        ],
        total=total,
        limit=budget.limit,
        rank=budget.rank,
    )
