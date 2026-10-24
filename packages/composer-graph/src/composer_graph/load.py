"""Write the gold graph rows into Neo4j.

A load replaces the whole graph, the same way promote replaces gold: the
database is emptied, constraints made sure of, nodes created, and then the
relationships between them. Every write is a batched ``UNWIND $rows`` so the
round trips scale with the batch count, not the row count.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from itertools import islice
from typing import LiteralString, TypeVar, cast

from neo4j import Driver, ManagedTransaction
from sqlalchemy.orm import Session

from .rows import (
    CLAIM_TYPES,
    NODE_LABELS,
    RelGroup,
    Row,
    Skipped,
    claim_relationships,
    composed_by,
    concert_nodes,
    entity_nodes,
    performed_in,
    programmed,
    recording_nodes,
    work_nodes,
)

log = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5_000

T = TypeVar("T")


@dataclass
class GraphStats:
    nodes: Counter[str] = field(default_factory=Counter[str])
    relationships: Counter[str] = field(default_factory=Counter[str])
    skipped: Skipped = field(default_factory=Skipped)

    def lines(self) -> Iterator[str]:
        """The stats as the CLI prints them, one count per line."""
        for label, count in sorted(self.nodes.items()):
            yield f"(:{label}) {count}"
        for rel_type, count in sorted(self.relationships.items()):
            yield f"[:{rel_type}] {count}"
        yield f"skipped: unmatched work mentions {self.skipped.unmatched_mentions}"
        yield f"skipped: works whose composer was pruned {self.skipped.orphan_works}"
        for reason, count in sorted(self.skipped.other.items()):
            yield f"skipped: {reason} {count}"


def _batches(rows: Iterable[T], size: int) -> Iterator[list[T]]:
    iterator = iter(rows)
    while batch := list(islice(iterator, size)):
        yield batch


def _cypher(text: str) -> LiteralString:
    """Labels and relationship types can't be query parameters, so they are
    formatted in — only ever from the fixed tables in ``rows``, never data."""
    return cast(LiteralString, text)


def _write(driver: Driver, database: str | None, query: LiteralString, rows: list[Row]) -> None:
    def work(tx: ManagedTransaction) -> None:
        tx.run(query, rows=rows).consume()

    with driver.session(database=database) as session:
        session.execute_write(work)


def _reset(driver: Driver, database: str | None) -> None:
    with driver.session(database=database) as session:
        # In batches of its own: one transaction deleting a million nodes would
        # need the whole delete set in heap.
        session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
        for label in NODE_LABELS:
            session.run(
                _cypher(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )
            ).consume()


def _create_nodes(
    driver: Driver, database: str | None, rows: Iterable[tuple[str, Row]], stats: GraphStats, batch_size: int
) -> None:
    for batch in _batches(rows, batch_size):
        by_label: dict[str, list[Row]] = {}
        for label, row in batch:
            by_label.setdefault(label, []).append(row)
        for label, label_rows in by_label.items():
            _write(
                driver, database, _cypher(f"UNWIND $rows AS row CREATE (n:{label}) SET n = row"), label_rows
            )
            stats.nodes[label] += len(label_rows)


def _create_relationships(
    driver: Driver,
    database: str | None,
    rows: Iterable[tuple[RelGroup, Row]],
    stats: GraphStats,
    batch_size: int,
) -> None:
    for batch in _batches(rows, batch_size):
        by_group: dict[RelGroup, list[Row]] = {}
        for group, row in batch:
            by_group.setdefault(group, []).append(row)
        for group, group_rows in by_group.items():
            query = _cypher(
                f"UNWIND $rows AS row "
                f"MATCH (a:{group.start} {{id: row.from}}) MATCH (b:{group.end} {{id: row.to}}) "
                f"CREATE (a)-[r:{group.type}]->(b) SET r = row.props"
            )
            _write(driver, database, query, group_rows)
            stats.relationships[group.type] += len(group_rows)


# How far each node reaches, one property per relation family. They stay
# separate because they count different things: a composer who also performs
# has a times_performed in the thousands and a handful of appearances, and
# ranking a performed edge against the appearances is how the first
# connections read path came to promote Beethoven rather than demote him.
#
# times_performed  performances of the node's music (PROGRAMMED.times summed)
# performances     performances the node took part in, of anyone's music
# appearances      distinct events the node is billed at
# claim_degree     distinct entities linked to it by a claim, has_profession
#                  excepted (the hub that would make the count meaningless)
_REACH_QUERIES: tuple[LiteralString, ...] = (
    "MATCH (c)<-[:COMPOSED_BY]-(:Work)<-[r:PROGRAMMED]-() "
    "WITH c, sum(r.times) AS n SET c.times_performed = n",
    "MATCH (p)-[:PERFORMED_IN]->(e) WITH DISTINCT p, e "
    "MATCH (e)-[r:PROGRAMMED]->(:Work) WITH p, sum(r.times) AS n SET p.performances = n",
    "MATCH (p)-[:PERFORMED_IN]->(e) WITH p, count(DISTINCT e) AS n SET p.appearances = n",
)


def claim_pattern() -> LiteralString:
    """The claim relationship types as a Cypher type union, has_profession left out."""
    return _cypher("|".join(rel for predicate, rel in CLAIM_TYPES.items() if predicate != "has_profession"))


def _annotate_reach(driver: Driver, database: str | None) -> None:
    """Store each node's reach: the denominators the connections ranking uses."""
    claim_degree = _cypher(
        f"MATCH (n)-[:{claim_pattern()}]-(other) WITH n, count(DISTINCT other) AS n_other "
        "SET n.claim_degree = n_other"
    )
    with driver.session(database=database) as session:
        for query in (*_REACH_QUERIES, claim_degree):
            session.run(query).consume()


def load_graph(
    session: Session, driver: Driver, *, database: str | None = None, batch_size: int = DEFAULT_BATCH_SIZE
) -> GraphStats:
    """Replace the Neo4j graph with one built from the gold ``session``."""
    stats = GraphStats()
    _reset(driver, database)
    log.info("neo4j: emptied, constraints in place")

    _create_nodes(driver, database, entity_nodes(session), stats, batch_size)
    for label, rows in (
        ("Work", work_nodes(session)),
        ("Concert", concert_nodes(session)),
        ("Recording", recording_nodes(session)),
    ):
        _create_nodes(driver, database, ((label, row) for row in rows), stats, batch_size)
    log.info("neo4j: nodes %s", dict(stats.nodes))

    for relationships in (composed_by, performed_in, programmed, claim_relationships):
        _create_relationships(driver, database, relationships(session, stats.skipped), stats, batch_size)
    log.info("neo4j: relationships %s", dict(stats.relationships))

    _annotate_reach(driver, database)
    log.info("neo4j: reach annotated")
    return stats
