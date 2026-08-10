"""The Neo4j side: constraints, wipe, and batched writes.

The driver is imported lazily so the rest of the package — and the admin API
that reports on it — works with the ``neo4j`` extra uninstalled.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from .config import ExportConfig
from .mapping import NodeBatch, RelBatch
from .model import NODE_LABELS

log = logging.getLogger(__name__)

# Deleting ~190k nodes in one transaction exhausts the heap on a small
# instance; CALL IN TRANSACTIONS commits in slices instead.
WIPE_CYPHER = """
MATCH (n)
CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS
"""

MERGE_NODES = """
UNWIND $rows AS row
MERGE (n:`{label}` {{id: row.id}})
SET n += row.props
"""

MERGE_RELATIONSHIPS = """
UNWIND $rows AS row
MATCH (a:`{start_label}` {{id: row.start}})
MATCH (b:`{end_label}` {{id: row.end}})
MERGE (a)-[r:`{rel_type}`]->(b)
SET r += row.props
"""


class GraphWriter:
    """Runs Cypher against a target database.

    A thin seam over the driver: the driver's ``execute_query`` is heavily
    overloaded, which makes it awkward to type against directly and impossible
    to fake precisely. Everything above this class talks to :meth:`run`, so
    tests substitute a recording stub and never need a live instance.
    """

    def __init__(self, driver: Any, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    def run(self, cypher: str, **params: Any) -> None:
        self._execute(cypher, params)

    def scalar(self, cypher: str, **params: Any) -> int:
        """Run a query returning one number — the counts the stats report."""
        records, _summary, _keys = self._execute(cypher, params)
        return int(records[0][0]) if records else 0

    def run_autocommit(self, cypher: str, **params: Any) -> None:
        """Run a statement that must own its transactions.

        ``CALL { … } IN TRANSACTIONS`` manages its own commits, so it is only
        legal in an implicit transaction — ``execute_query`` wraps everything in
        an explicit one and the server rejects it with TransactionStartFailed.
        A plain session run is the auto-commit path.
        """
        kwargs = {} if self._database is None else {"database": self._database}
        with self._driver.session(**kwargs) as session:
            session.run(cypher, **params).consume()

    def _execute(self, cypher: str, params: dict[str, Any]) -> Any:
        # Omitting database_ resolves to the connection's home database; passing
        # a wrong name (e.g. "neo4j" on an Aura instance) fails with
        # DatabaseNotFound, so only send it when it was configured.
        if self._database is not None:
            params["database_"] = self._database
        return self._driver.execute_query(cypher, **params)

    def close(self) -> None:
        self._driver.close()


def graph_size(writer: GraphWriter) -> tuple[int, int]:
    """What the target actually holds, which is not what was sent.

    ``MERGE`` collapses duplicate edges: a concert that lists the same work
    twice (an encore, or a multi-movement programme split across lines) sends
    two rows and keeps one relationship. Reporting the rows sent would overstate
    the graph by ~11k relationships on the current gold build, so the stats ask
    the database instead.
    """
    nodes = writer.scalar("MATCH (n) RETURN count(n)")
    relationships = writer.scalar("MATCH ()-[r]->() RETURN count(r)")
    return nodes, relationships


def connect(config: ExportConfig) -> GraphWriter:
    """Open a writer. Raises ImportError when the ``neo4j`` extra is missing."""
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise ImportError("the neo4j driver is not installed; run `uv sync --extra neo4j`") from exc
    driver = GraphDatabase.driver(config.uri, auth=(config.user, config.password))
    return GraphWriter(driver, config.database)


def verify(config: ExportConfig) -> None:
    """Raise if the configured instance cannot be reached or authenticated."""
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise ImportError("the neo4j driver is not installed; run `uv sync --extra neo4j`") from exc
    with GraphDatabase.driver(config.uri, auth=(config.user, config.password)) as driver:
        driver.verify_connectivity()


def create_constraints(writer: GraphWriter) -> None:
    """One uniqueness constraint per label — also the index every MERGE needs."""
    for label in NODE_LABELS:
        writer.run(
            f"CREATE CONSTRAINT `{label.lower()}_id` IF NOT EXISTS "
            f"FOR (n:`{label}`) REQUIRE n.id IS UNIQUE"
        )


def wipe(writer: GraphWriter) -> None:
    """Empty the target database.

    The export is a full rebuild, mirroring promote. Aura gives a free instance
    exactly one database, so there is no atomic swap to hide behind: the window
    between wipe and reload is visible, and that is the trade for the button
    doing what its label says.
    """
    writer.run_autocommit(WIPE_CYPHER)


def write_nodes(writer: GraphWriter, batches: Iterator[NodeBatch]) -> int:
    written = 0
    for batch in batches:
        writer.run(MERGE_NODES.format(label=batch.label), rows=batch.rows)
        written += len(batch.rows)
        log.debug("wrote %d :%s nodes (%d total)", len(batch.rows), batch.label, written)
    return written


def write_relationships(writer: GraphWriter, batches: Iterator[RelBatch]) -> int:
    written = 0
    for batch in batches:
        writer.run(
            MERGE_RELATIONSHIPS.format(
                start_label=batch.start_label,
                end_label=batch.end_label,
                rel_type=batch.type,
            ),
            rows=batch.rows,
        )
        written += len(batch.rows)
        log.debug("wrote %d :%s relationships (%d total)", len(batch.rows), batch.type, written)
    return written
