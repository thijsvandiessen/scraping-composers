from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from .db import get_engine, init_db
from .ingest import run_ingest
from .models import Claim, Entity, EntityRecord, IngestRun, Source
from .normalize import dedup_key
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


EntityClaims = list[tuple[Entity, list[tuple[str, str | None, str | None, str, int | None]]]]


def entity_claims(
    session: Session,
    name: str,
    *,
    kind: str | None = None,
    predicate: str | None = None,
    source: str | None = None,
    limit: int = 10,
) -> EntityClaims:
    """For each entity matching ``name`` (exact dedup key or label substring),
    its outgoing claims as (predicate, value, object_label, source_name,
    record_id). The ingest already collapses identical assertions per source,
    so competing values for a predicate line up one row per source that made
    them — the basis for deciding which to trust."""
    query = select(Entity).where(or_(Entity.dedup_key == dedup_key(name), Entity.label.ilike(f"%{name}%")))
    if kind is not None:
        query = query.where(Entity.kind == kind)
    entities = session.scalars(query.order_by(Entity.kind, Entity.label).limit(limit)).all()

    obj = aliased(Entity)
    results: EntityClaims = []
    for entity in entities:
        claims = (
            select(Claim.predicate, Claim.value, obj.label, Source.name, Claim.record_id)
            .join(Source, Source.id == Claim.source_id)
            .outerjoin(obj, obj.id == Claim.object_id)
            .where(Claim.subject_id == entity.id)
            .order_by(Claim.predicate, Source.name, Claim.value)
        )
        if predicate is not None:
            claims = claims.where(Claim.predicate == predicate)
        if source is not None:
            claims = claims.where(Source.name == source)
        results.append((entity, list(session.execute(claims).tuples())))
    return results


def cmd_claims(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        results = entity_claims(
            session,
            args.name,
            kind=args.kind,
            predicate=args.predicate,
            source=args.source,
            limit=args.limit,
        )
    if not results:
        print(f"no entity matching {args.name!r}")
        return 1
    for entity, rows in results:
        print(f"\nentity {entity.id}: {entity.label} ({entity.kind})")
        if not rows:
            print("  (no matching claims)")
        current = None
        for predicate, value, object_label, source_name, record_id in rows:
            if predicate != current:
                print(f"  {predicate}:")
                current = predicate
            shown = object_label if object_label is not None else (value or "")
            print(f"    {shown:<34} {source_name:<14} record={record_id}")
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

    p_claims = sub.add_parser(
        "claims", help="show an entity's claims and which source asserts each (to compare and choose)"
    )
    p_claims.add_argument("name", help="entity name (exact dedup-key match or label substring)")
    p_claims.add_argument("--kind", help="restrict to an entity kind (person, work, place, ...)")
    p_claims.add_argument("--predicate", help="restrict to one predicate (e.g. born_on)")
    p_claims.add_argument("--source", help="restrict to one source (e.g. wikidata)")
    p_claims.add_argument("--limit", type=int, default=10, help="max matching entities to show")
    p_claims.set_defaults(func=cmd_claims)

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
