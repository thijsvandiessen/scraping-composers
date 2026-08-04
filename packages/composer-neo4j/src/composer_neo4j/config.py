"""Connection and scope settings for the gold → Neo4j export."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from composer_config import settings

# Neo4j Aura's free tier caps an instance at 200k nodes and 400k relationships.
# The full gold database lands at ~190k / ~397k — inside the caps, but with so
# little headroom that one more source would push a run over mid-write. The
# export therefore defaults to performed works only (~68k / ~276k) and reports
# both counts, so the ceiling is visible before it is hit.
AURA_FREE_MAX_NODES = 200_000
AURA_FREE_MAX_RELATIONSHIPS = 400_000


class ExportNotConfiguredError(RuntimeError):
    """Raised when an export is requested without connection settings."""


class ExportTooLargeError(RuntimeError):
    """Raised when the mapped graph would exceed the target's capacity."""


@dataclass(frozen=True)
class ExportConfig:
    """Per-run knobs of the export.

    ``include_unperformed_works`` is the one that matters: gold holds 136k works
    but only ~15k of them ever appear on a concert or recording programme. The
    rest are catalogue entries with no edges other than their composer, so they
    inflate the graph without making it more traversable.
    """

    uri: str
    user: str
    password: str
    # None means "the connection's home database", which is what an Aura
    # instance wants: it names its database after the instance id, not "neo4j".
    database: str | None = None
    include_unperformed_works: bool = False
    wipe_first: bool = True
    batch_size: int = 1_000

    def with_overrides(self, **overrides: Any) -> ExportConfig:
        """A copy with the non-``None`` entries of ``overrides`` applied."""
        given = {key: value for key, value in overrides.items() if value is not None}
        return replace(self, **given) if given else self


def is_configured() -> bool:
    """Whether the environment carries enough to reach a Neo4j instance."""
    return bool(settings.neo4j_uri and settings.neo4j_password)


def config_from_settings(**overrides: Any) -> ExportConfig:
    """Build a config from the environment, raising if it isn't configured.

    Callers that need to *report* an unconfigured target rather than fail on it
    should check :func:`is_configured` first.
    """
    uri, password = settings.neo4j_uri, settings.neo4j_password
    if not uri or not password:
        raise ExportNotConfiguredError(
            "Neo4j is not configured: set NEO4J_URI and NEO4J_PASSWORD "
            "(or NEO4J_API_KEY) in the environment"
        )
    base = ExportConfig(
        uri=uri,
        user=settings.neo4j_user,
        password=password,
        database=settings.neo4j_database,
    )
    return base.with_overrides(**overrides)
