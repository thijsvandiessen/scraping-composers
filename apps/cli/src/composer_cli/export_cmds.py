"""Exports out of the gold database, for tools that live outside the pipeline."""

import argparse
import logging
from dataclasses import asdict

from composer_gold import KumuConfig, export_kumu, gold_engine, gold_target
from composer_graph import SnapshotStats, load_graph, snapshot_connections
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)


def cmd_export_kumu(args: argparse.Namespace) -> int:
    target = gold_target(args.gold_url)
    if not target.exists():
        # Asked of the target, never by connecting: opening a sqlite URL
        # creates the file, so a typo in --gold-url would leave an empty
        # database behind and export nothing.
        print(f"no gold database at {target.describe()} — run `composer-ingest promote` first")
        return 1
    engine = gold_engine(args.gold_url)
    try:
        with sessionmaker(engine)() as session:
            config = KumuConfig(
                performer_limit=args.limit,
                min_weight=args.min_weight,
                performances=args.performances,
                claims=args.claims,
            )
            stats = export_kumu(session, args.output, config)
    except Exception:
        log.exception("kumu export failed")
        return 1
    finally:
        engine.dispose()
    print(f"kumu blueprint written to {args.output}")
    for key, value in asdict(stats).items():
        print(f"  {key.replace('_', ' '):<20} {value}")
    return 0


def _neo4j_driver(args: argparse.Namespace) -> Driver | None:
    """A driver for ``--neo4j-url``, or None (reported) when it can't be reached."""
    driver = GraphDatabase.driver(
        args.neo4j_url,
        auth=(args.neo4j_user, args.neo4j_password),
        # The load re-asserts its constraints every run; the server's "already
        # exists" notices for that are noise.
        notifications_min_severity="WARNING",
    )
    try:
        driver.verify_connectivity()
    except (ServiceUnavailable, Neo4jError) as exc:
        print(f"cannot reach neo4j at {args.neo4j_url} ({exc}) — is `docker compose up -d neo4j` running?")
        driver.close()
        return None
    return driver


def _print_snapshot(stats: SnapshotStats) -> None:
    print(
        f"connections snapshot stored in gold: {stats.snapshots} of {stats.entities} entities "
        f"have neighbours, {stats.edges} edges kept"
    )


def cmd_export_neo4j(args: argparse.Namespace) -> int:
    """Load gold into Neo4j, then snapshot every entity's connections back into gold."""
    target = gold_target(args.gold_url)
    if not target.exists():
        print(f"no gold database at {target.describe()} — run `composer-ingest promote` first")
        return 1
    if (driver := _neo4j_driver(args)) is None:
        return 1
    engine = gold_engine(args.gold_url)
    try:
        with sessionmaker(engine)() as session:
            stats = load_graph(session, driver, database=args.database, batch_size=args.batch_size)
            print(f"gold loaded into neo4j at {args.neo4j_url}")
            for line in stats.lines():
                print(f"  {line}")
            snapshot = snapshot_connections(session, driver, database=args.database, workers=args.workers)
    except Exception:
        log.exception("neo4j export failed")
        return 1
    finally:
        engine.dispose()
        driver.close()
    _print_snapshot(snapshot)
    return 0


def cmd_snapshot_connections(args: argparse.Namespace) -> int:
    """Re-take the connections snapshot from a graph that is already loaded."""
    target = gold_target(args.gold_url)
    if not target.exists():
        print(f"no gold database at {target.describe()} — run `composer-ingest promote` first")
        return 1
    if (driver := _neo4j_driver(args)) is None:
        return 1
    engine = gold_engine(args.gold_url)
    try:
        with sessionmaker(engine)() as session:
            snapshot = snapshot_connections(session, driver, database=args.database, workers=args.workers)
    except Exception:
        log.exception("connections snapshot failed")
        return 1
    finally:
        engine.dispose()
        driver.close()
    _print_snapshot(snapshot)
    return 0
