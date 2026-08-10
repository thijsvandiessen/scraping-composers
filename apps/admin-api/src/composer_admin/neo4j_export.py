"""The gold → Neo4j export, as the admin API sees it.

Kept beside ``build_routes`` rather than inside it: the export has its own
configuration story (an external instance that may be absent, unreachable, or
too small), and that reporting is most of the code here.
"""

import logging
from urllib.parse import urlsplit

from composer_gold import DEFAULT_GOLD_DB_PATH
from composer_neo4j import (
    ExportConfig,
    config_from_settings,
    export_to_neo4j,
    is_configured,
    read_export_manifest,
    verify,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .schemas import Neo4jExportOptions, Neo4jStatus

log = logging.getLogger(__name__)


def _safe_uri(uri: str | None) -> str | None:
    """The instance host, without anything credential-shaped."""
    if not uri:
        return None
    parts = urlsplit(uri)
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else None


def _reachability(config: ExportConfig) -> tuple[bool, str | None]:
    try:
        verify(config)
    except ImportError:
        return False, "the neo4j driver is not installed (uv sync --extra neo4j)"
    except Exception as exc:  # the driver raises a wide family of connection errors
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def neo4j_status(probe: bool = True) -> Neo4jStatus:
    """Configuration, reachability and the last export's manifest.

    ``probe`` opens a connection to check the instance is actually reachable;
    the dashboard wants that, a status poll during a running export does not.
    """
    manifest = read_export_manifest()

    def status(
        configured: bool, reachable: bool | None, uri: str | None, detail: str | None
    ) -> Neo4jStatus:
        return Neo4jStatus(
            configured=configured,
            reachable=reachable,
            uri=uri,
            detail=detail,
            status=manifest.status if manifest else None,
            started_at=manifest.started_at if manifest else None,
            finished_at=manifest.finished_at if manifest else None,
            error=manifest.error if manifest else None,
            stats=manifest.stats if manifest else {},
        )

    if not is_configured():
        return status(False, None, None, "set NEO4J_URI and NEO4J_PASSWORD (or NEO4J_API_KEY)")

    config = config_from_settings()
    reachable, detail = _reachability(config) if probe else (None, None)
    return status(True, reachable, _safe_uri(config.uri), detail)


def export_in_background(config: ExportConfig) -> None:
    """Run the export; status lives in the export manifest."""
    engine = create_engine(f"sqlite:///{DEFAULT_GOLD_DB_PATH}")
    try:
        with Session(engine) as gold:
            export_to_neo4j(gold, config)
    except Exception:
        # Recorded as a failed manifest by export_to_neo4j; log for the console.
        log.exception("background neo4j export failed")
    finally:
        engine.dispose()


def start_neo4j_export(options: Neo4jExportOptions | None) -> ExportConfig:
    """Resolve the request body into a config for the background task."""
    opts = options or Neo4jExportOptions()
    return config_from_settings(
        uri=opts.uri,
        include_unperformed_works=opts.include_unperformed_works,
        wipe_first=opts.wipe_first,
    )
