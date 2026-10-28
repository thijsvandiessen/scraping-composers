"""The gold database as a Neo4j property graph, for exploring connections.

``load_graph`` replaces a Neo4j database with the whole of gold: entities
(people, ensembles, places, genres, ...), works, and the concerts and
recordings that tie performers to the works they played. Relationships are
the facts gold holds, not scores derived from them — how strongly two people
are connected is left to the queries (see ``queries.cypher``).

``snapshot_connections`` then asks the graph for each entity's strongest
neighbours and stores the answers in gold, which is where the consumer API
reads them from: production serves the graph without running Neo4j.
"""

from .connections import DEFAULT_WORKERS, SnapshotStats, snapshot_connections
from .load import DEFAULT_BATCH_SIZE, GraphStats, claim_pattern, load_graph
from .rows import CLAIM_TYPES, ENTITY_LABELS, NODE_LABELS

__all__ = [
    "CLAIM_TYPES",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_WORKERS",
    "ENTITY_LABELS",
    "NODE_LABELS",
    "GraphStats",
    "SnapshotStats",
    "claim_pattern",
    "load_graph",
    "snapshot_connections",
]
