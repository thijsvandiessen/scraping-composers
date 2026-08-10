"""Export the curated gold database into a Neo4j property graph.

Gold answers "who is this person, what did they compose, what was on this
programme" well. It answers "which conductors connect these two composers"
badly — that is a traversal, and a relational schema pays for every hop.

This package copies gold into Neo4j, where the hops are the query language. It
is strictly additive: nothing here writes to gold, and the consumer APIs are
untouched. The one modelling rule is the same one gold already follows — what a
source *asserted* becomes a relationship, what we *computed* becomes a property.

    uv sync --extra neo4j
    uv run composer-ingest promote-neo4j
"""

from .config import (
    AURA_FREE_MAX_NODES,
    AURA_FREE_MAX_RELATIONSHIPS,
    ExportConfig,
    ExportNotConfiguredError,
    ExportTooLargeError,
    config_from_settings,
    is_configured,
)
from .export import (
    DEFAULT_MANIFEST_KEY,
    ExportStats,
    check_capacity,
    count_nodes,
    count_relationships,
    export_to_neo4j,
    read_export_manifest,
)
from .writer import verify

__all__ = [
    "AURA_FREE_MAX_NODES",
    "AURA_FREE_MAX_RELATIONSHIPS",
    "DEFAULT_MANIFEST_KEY",
    "ExportConfig",
    "ExportNotConfiguredError",
    "ExportStats",
    "ExportTooLargeError",
    "check_capacity",
    "config_from_settings",
    "count_nodes",
    "count_relationships",
    "export_to_neo4j",
    "is_configured",
    "read_export_manifest",
    "verify",
]
