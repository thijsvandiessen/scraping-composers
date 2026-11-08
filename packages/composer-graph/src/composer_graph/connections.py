"""Snapshot every entity's neighbourhood out of Neo4j into gold.

The consumer API's ``/v1/entities/{id}/connections`` serves these snapshots
(:class:`composer_models.EntityConnections`), so production needs no graph
server: Neo4j is where the graph is built and explored, and this module asks
it the connections questions once per entity, right after the load.

What the queries find: a performer reaches the composers it programmed
through its concerts' (or recordings') works, performers billed together
reach each other, and the claims (``born_in``, ``student_of``,
``has_genre``, ...) hang the biographical context off both ends.

Every edge is scored both ways the API ranks:

``weight``
    The raw co-occurrence, which surfaces the famous.
``affinity``
    The weight divided by the square root of the neighbour's reach *in the
    same relation family* — how often a composer is performed at all, how
    many performances a performer gives, how many events a stage partner
    appears at — which demotes the hubs everyone connects to and lets a
    pairing that is *distinctive* come out on top. The reach is stored on
    each node by the loader, one property per family; comparing an edge with
    a reach from another family is the mistake the read path was rewritten
    to stop making.

Claim edges are scored differently, because their weight (how many sources
assert the claim) is almost always one or two. A place, genre or movement is
discounted by how many entities it is linked to — Vienna says less about a
person than a small town does. A person or ensemble is the other way round:
on Vienna's page the people born there are ranked by how widely they are
performed, so Beethoven and Brahms come before a name nobody has heard. A
neighbour linked by several claims (born *and* died there) is one edge, with
all its relations listed.

Only the best ``POOL_SIZE`` edges per relation under each ranking are kept,
with the proof (roles, a few works) for those; the weights of all of them are
counted, so the API can still say how many there were.
"""

import logging
import math
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, LiteralString, cast

from composer_models import CLAIMS, POOL_SIZE, ConnectionEdge, ConnectionsPayload, EntityConnections
from neo4j import Driver
from sqlalchemy import Table, delete, insert
from sqlalchemy.orm import Session

from .load import claim_pattern
from .rows import CLAIM_TYPES, ENTITY_LABELS

log = logging.getLogger(__name__)

# Relations, in the order the graph should read them.
PERFORMED = "performed"  # this entity performed that composer's music
PERFORMED_BY = "performed_by"  # that performer played this composer's music
APPEARED_WITH = "appeared_with"  # shared a concert or a recording session

RANKS = ("affinity", "weight")

# Proof snippets carried per edge. Three is enough to show the reader what the
# edge is made of without turning the payload into a concert listing.
VIA_CAP = 3

# Neo4j answers one entity's queries in milliseconds; running a few entities
# at once is what keeps a full snapshot to minutes.
DEFAULT_WORKERS = 8

_KINDS = {label: kind for kind, label in ENTITY_LABELS.items()}
_PREDICATES = {rel_type: predicate for predicate, rel_type in CLAIM_TYPES.items()}

# Performances are counted per event and work (PROGRAMMED.times), not per
# credit: a soloist who also conducted the night is one performance, two roles.
# Each comes with the reach property its affinity is divided by; below 1 it is
# clamped, so a neighbour the loader didn't annotate ranks by its raw weight.
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
_PERFORMANCE_TAIL: LiteralString = """
WHERE weight >= 1
RETURN elementId(n) AS node, n.id AS id, n.label AS label, labels(n)[0] AS kind,
       weight, roles, toFloat(coalesce(n[$reach], 1)) AS reach
"""

_CLAIMS: LiteralString = """
MATCH (f:$FOCUS {id: $id})-[r:$CLAIMS]-(n)
WHERE n <> f
WITH n, r, CASE WHEN startNode(r) = f THEN 'out' ELSE 'in' END AS direction,
     coalesce(r.sources, []) AS sources
ORDER BY size(sources) DESC, type(r)
WITH n, collect({type: type(r), direction: direction}) AS claims,
     sum(size(sources)) AS weight, reduce(acc = [], s IN collect(sources) | acc + s) AS sources
WHERE weight >= 1
RETURN elementId(n) AS node, n.id AS id, n.label AS label, labels(n)[0] AS kind,
       claims, weight, sources,
       CASE WHEN n:Person OR n:Ensemble
            THEN log(2 + coalesce(n.times_performed, 0) + coalesce(n.appearances, 0))
            ELSE 1 / sqrt(toFloat(CASE WHEN coalesce(n.claim_degree, 1) < 1 THEN 1 ELSE n.claim_degree END))
       END AS factor
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
class SnapshotStats:
    entities: int = 0  # graph nodes asked about
    snapshots: int = 0  # of those with at least one neighbour
    edges: int = 0  # edges kept across all snapshots


def _cypher(template: LiteralString, focus: str) -> LiteralString:
    """Labels and relationship types can't be parameters, so they are formatted
    in — the focus label from ``ENTITY_LABELS``, the types from ``CLAIM_TYPES``,
    never request data. A label is what lets the lookup use the id constraint;
    without one Neo4j scans every node."""
    return cast(LiteralString, template.replace("$FOCUS", focus).replace("$CLAIMS", claim_pattern()))


def _kind(node_label: str) -> str:
    """The gold entity kind a node label stands for (``Person`` -> ``person``)."""
    return _KINDS.get(node_label) or node_label.lower()


class _Snapshotter:
    def __init__(self, driver: Driver, database: str | None) -> None:
        self.driver = driver
        self.database = database

    def run(self, query: LiteralString, **params: Any) -> list[dict[str, Any]]:
        # Notifications off: a claim type no node uses yet draws an "unknown
        # relationship type" warning, which would repeat for every entity.
        with self.driver.session(database=self.database, notifications_min_severity="OFF") as session:
            return [record.data() for record in session.run(query, params)]

    def performance_edges(
        self, focus: str, entity_id: str, relation: str
    ) -> list[tuple[str, ConnectionEdge]]:
        head, reach = _PERFORMANCE_QUERIES[relation]
        rows = self.run(_cypher(head + _PERFORMANCE_TAIL, focus), id=entity_id, reach=reach)
        direction = "in" if relation == PERFORMED_BY else "out"
        return [
            (
                row["node"],
                ConnectionEdge(
                    family=relation,
                    entity_id=row["id"],
                    label=row["label"],
                    kind=_kind(row["kind"]),
                    relations=[relation],
                    directions={relation: direction},
                    weight=row["weight"],
                    scores={
                        "affinity": row["weight"] / math.sqrt(max(row["reach"], 1.0)),
                        "weight": float(row["weight"]),
                    },
                    roles=sorted(set(row["roles"])),
                    via=[],
                ),
            )
            for row in rows
        ]

    def claim_edges(self, focus: str, entity_id: str) -> list[ConnectionEdge]:
        """One edge per neighbour, its claims collapsed, strongest claim first."""
        edges: list[ConnectionEdge] = []
        for row in self.run(_cypher(_CLAIMS, focus), id=entity_id):
            directions: dict[str, str] = {}
            for claim in row["claims"]:
                directions.setdefault(_PREDICATES[claim["type"]], claim["direction"])
            edges.append(
                ConnectionEdge(
                    family=CLAIMS,
                    entity_id=row["id"],
                    label=row["label"],
                    kind=_kind(row["kind"]),
                    relations=list(directions),
                    directions=directions,
                    weight=row["weight"],
                    scores={"affinity": row["weight"] * row["factor"], "weight": float(row["weight"])},
                    roles=[],
                    via=list(dict.fromkeys(row["sources"]))[:VIA_CAP],
                )
            )
        return edges

    def attach_proof(self, focus: str, entity_id: str, edges: dict[str, ConnectionEdge]) -> None:
        """Hang the roles and a few real performances off each kept performance edge."""
        for relation, pattern in _PROOF.items():
            by_node = {node: e for node, e in edges.items() if e["family"] == relation}
            if not by_node:
                continue
            query = (
                "MATCH (f:$FOCUS {id: $id}) UNWIND $nodes AS node MATCH (n) WHERE elementId(n) = node "
                "CALL (f, n) { " + pattern + _PROOF_TAIL
            )
            for row in self.run(_cypher(query, focus), id=entity_id, nodes=list(by_node)):
                edge = by_node[row["node"]]
                edge["roles"] = sorted(row["roles"])
                edge["via"] = _newest(row["works"])

    def snapshot(self, focus: str, entity_id: str) -> ConnectionsPayload:
        weights: dict[str, Counter[int]] = {}
        kept: dict[str, ConnectionEdge] = {}  # by element id, per family below
        for relation in _PERFORMANCE_QUERIES:
            found = self.performance_edges(focus, entity_id, relation)
            weights[relation] = Counter(edge["weight"] for _, edge in found)
            by_key = {f"{relation}:{node}": edge for node, edge in found}
            for key in _pool(by_key):
                kept[key] = by_key[key]
        claims = {edge["entity_id"]: edge for edge in self.claim_edges(focus, entity_id)}
        weights[CLAIMS] = Counter(edge["weight"] for edge in claims.values())
        # Pooled per predicate, so asking for one relation still finds its best
        # edges; the overall best are among those, every edge having a predicate.
        for predicate in dict.fromkeys(p for edge in claims.values() for p in edge["relations"]):
            having = {key: edge for key, edge in claims.items() if predicate in edge["relations"]}
            weights[predicate] = Counter(edge["weight"] for edge in having.values())
            for key in _pool(having):
                kept[f"{CLAIMS}:{key}"] = claims[key]

        # The proof query looks the neighbour up by element id, which the
        # performance keys carry after the family.
        proof = {key.partition(":")[2]: edge for key, edge in kept.items() if edge["family"] in _PROOF}
        self.attach_proof(focus, entity_id, proof)
        edges = sorted(kept.values(), key=lambda e: (-e["scores"]["affinity"], -e["weight"], e["entity_id"]))
        return ConnectionsPayload(
            edges=edges,
            weights={
                family: {str(weight): n for weight, n in sorted(counts.items())}
                for family, counts in weights.items()
                if counts
            },
        )


def _pool(edges: dict[str, ConnectionEdge]) -> set[str]:
    """The keys of the best ``POOL_SIZE`` edges under either ranking."""
    keys: set[str] = set()
    for rank in RANKS:
        ranked = sorted(
            edges, key=lambda k: (-edges[k]["scores"][rank], -edges[k]["weight"], edges[k]["entity_id"])
        )
        keys.update(ranked[:POOL_SIZE])
    return keys


def _newest(works: list[list[str | None]]) -> list[str]:
    """Up to ``VIA_CAP`` "Title (year)" snippets, newest first, undated last."""
    named = sorted(((title, year) for title, year in works if title is not None), key=lambda w: w[0])
    # Stable, so equal years keep the title order; an undated "" sorts last.
    named.sort(key=lambda w: w[1] or "", reverse=True)
    snippets = [f"{title} ({year})" if year else title for title, year in named]
    return list(dict.fromkeys(snippets))[:VIA_CAP]


def _focuses(snapshotter: _Snapshotter) -> Iterator[tuple[str, str]]:
    """(node label, entity id) for every entity node in the graph."""
    for label in ENTITY_LABELS.values():
        for row in snapshotter.run(cast(LiteralString, f"MATCH (n:{label}) RETURN n.id AS id ORDER BY n.id")):
            yield label, row["id"]


def _batches(items: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def snapshot_connections(
    session: Session,
    driver: Driver,
    *,
    database: str | None = None,
    workers: int = DEFAULT_WORKERS,
) -> SnapshotStats:
    """Replace gold's ``entity_connections`` with a snapshot of the loaded graph.

    Commits. The graph must be the projection of the gold ``session`` is
    bound to, so that every node id is an entity there.
    """
    snapshotter = _Snapshotter(driver, database)
    focuses = list(_focuses(snapshotter))
    stats = SnapshotStats(entities=len(focuses))
    log.info("connections: snapshotting %d entities", len(focuses))

    def one(focus: tuple[str, str]) -> dict[str, Any] | None:
        payload = snapshotter.snapshot(*focus)
        if not payload["edges"]:
            return None
        return {"entity_id": uuid.UUID(focus[1]), "payload": payload}

    # Promote creates the table; a gold promoted before it existed gets it here.
    cast(Table, EntityConnections.__table__).create(session.connection(), checkfirst=True)
    session.execute(delete(EntityConnections))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = (row for row in pool.map(one, focuses) if row is not None)
        for batch in _batches(rows, 500):
            session.execute(insert(EntityConnections), batch)
            stats.snapshots += len(batch)
            stats.edges += sum(len(row["payload"]["edges"]) for row in batch)
            if stats.snapshots % 5000 < len(batch):
                log.info("connections: %d snapshots written", stats.snapshots)
    session.commit()
    log.info("connections: %d snapshots, %d edges", stats.snapshots, stats.edges)
    return stats
