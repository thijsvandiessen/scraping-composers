import argparse

from composer_ingest.etl.db import get_engine, init_db
from composer_ingest.etl.models import (
    Claim,
    Entity,
    EntityRecord,
    IngestRun,
    PersonMatch,
    RawWorkMention,
    Source,
    Work,
    WorkTitle,
)
from composer_ingest.etl.normalize import dedup_key
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased


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

        works = session.scalar(select(func.count(Work.id)))
        titles = session.scalar(select(func.count(WorkTitle.id)))
        mentions = session.scalar(select(func.count(RawWorkMention.id)))
        print(f"works (resolved):        {works}")
        print(f"work titles (aliases):   {titles}")
        print(f"work mentions:           {mentions}")
        by_status = session.execute(
            select(RawWorkMention.match_status, func.count(RawWorkMention.id))
            .group_by(RawWorkMention.match_status)
            .order_by(RawWorkMention.match_status)
        ).all()
        for status, count in by_status:
            print(f"  {status}: {count}")

        linked = session.scalar(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None)))
        to_review = session.scalar(
            select(func.count(PersonMatch.id)).where(PersonMatch.status == "needs_review")
        )
        print(f"person duplicates linked: {linked}")
        print(f"person matches to review: {to_review}")
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
