from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, select

from .db import get_engine, init_db
from .ingest import run_ingest
from .models import Claim, Entity, EntityRecord, IngestRun, Source
from .sources import REGISTRY


def cmd_ingest(args: argparse.Namespace) -> int:
    source_module = REGISTRY[args.source]
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        run = run_ingest(session, source_module, max_pages=args.max_pages)
    return 0 if run.status == "completed" else 1


def cmd_stats(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        entities = session.scalar(select(func.count(Entity.id)))
        records = session.scalar(select(func.count(EntityRecord.id)))
        claims = session.scalar(select(func.count(Claim.id)))
        print(f"entities (deduplicated): {entities}")
        per_kind = session.execute(
            select(Entity.kind, func.count(Entity.id)).group_by(Entity.kind).order_by(Entity.kind)
        ).all()
        for kind, count in per_kind:
            print(f"  {kind}: {count}")
        print(f"claims:                  {claims}")
        print(f"source records:          {records}")
        per_source = session.execute(
            select(Source.name, func.count(EntityRecord.id))
            .join(EntityRecord, EntityRecord.source_id == Source.id)
            .group_by(Source.name)
        ).all()
        for name, count in per_source:
            print(f"  {name}: {count}")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        runs = session.execute(
            select(IngestRun, Source.name)
            .join(Source)
            .order_by(IngestRun.started_at.desc())
            .limit(args.limit)
        ).all()
        if not runs:
            print("no ingest runs yet")
            return 0
        print(f"{'id':>4}  {'source':<10} {'status':<10} {'started (UTC)':<20} {'seen':>7} {'new':>7}")
        for run, source_name in runs:
            started = run.started_at.strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"{run.id:>4}  {source_name:<10} {run.status:<10} {started:<20}"
                f" {run.records_seen:>7} {run.records_new:>7}"
            )
            if run.error:
                print(f"      error: {run.error}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="composer-ingest", description=__doc__)
    parser.add_argument(
        "--database-url",
        help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///composers.db)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="fetch a source and store its records")
    p_ingest.add_argument("source", choices=sorted(REGISTRY))
    p_ingest.add_argument("--max-pages", type=int, help="stop after N pages (for testing)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_stats = sub.add_parser("stats", help="show dataset counts")
    p_stats.set_defaults(func=cmd_stats)

    p_runs = sub.add_parser("runs", help="show the ingest run log")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
