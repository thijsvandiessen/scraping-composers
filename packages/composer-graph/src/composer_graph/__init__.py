"""The gold database as a Neo4j property graph, for exploring connections.

``load_graph`` replaces a Neo4j database with the whole of gold: entities
(people, ensembles, places, genres, ...), works, and the concerts and
recordings that tie performers to the works they played. Relationships are
the facts gold holds, not scores derived from them — how strongly two people
are connected is left to the queries (see ``queries.cypher``).
"""

from .load import DEFAULT_BATCH_SIZE, GraphStats, load_graph
from .rows import CLAIM_TYPES, ENTITY_LABELS, NODE_LABELS

__all__ = [
    "CLAIM_TYPES",
    "DEFAULT_BATCH_SIZE",
    "ENTITY_LABELS",
    "NODE_LABELS",
    "GraphStats",
    "load_graph",
]
