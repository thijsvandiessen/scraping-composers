"""Exports out of the gold database, for tools that live outside the pipeline."""

import argparse
import logging
from dataclasses import asdict

from composer_gold import KumuConfig, export_kumu, gold_engine, gold_target
from composer_graph import load_graph
from neo4j import GraphDatabase
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


def cmd_export_neo4j(args: argparse.Namespace) -> int:
    target = gold_target(args.gold_url)
    if not target.exists():
        print(f"no gold database at {target.describe()} — run `composer-ingest promote` first")
        return 1
    driver = GraphDatabase.driver(
        args.neo4j_url,
        auth=(args.neo4j_user, args.neo4j_password),
        # The load re-asserts its constraints every run; the server's "already
        # exists" notices for that are noise.
        notifications_min_severity="WARNING",
    )
    engine = gold_engine(args.gold_url)
    try:
        try:
            driver.verify_connectivity()
        except (ServiceUnavailable, Neo4jError) as exc:
            print(
                f"cannot reach neo4j at {args.neo4j_url} ({exc}) — is `docker compose up -d neo4j` running?"
            )
            return 1
        with sessionmaker(engine)() as session:
            stats = load_graph(session, driver, database=args.database, batch_size=args.batch_size)
    except Exception:
        log.exception("neo4j export failed")
        return 1
    finally:
        engine.dispose()
        driver.close()
    print(f"gold loaded into neo4j at {args.neo4j_url}")
    for line in stats.lines():
        print(f"  {line}")
    return 0
