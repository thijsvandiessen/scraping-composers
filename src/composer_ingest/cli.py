from __future__ import annotations

import argparse
import logging
import sys
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from .db import get_engine, init_db
from .ingest import new_work, run_ingest
from .models import Claim, Entity, EntityRecord, IngestRun, RawWorkMention, Source, Work, WorkTitle
from .normalize import dedup_key
from .sources import REGISTRY
from .works import Candidate, extract_features, normalize_title, resolve


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


def _work_features_line(work: Work) -> str:
    catalogue = f"{work.catalogue_prefix or ''} {work.catalogue_number or ''}".strip()
    pairs = (
        ("type", work.work_type),
        ("opus", work.opus_number),
        ("cat", catalogue or None),
        ("key", work.musical_key),
        ("no", str(work.number) if work.number is not None else None),
    )
    return "  ".join(f"{k}={v}" for k, v in pairs if v)


def cmd_works(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        composer = aliased(Entity)
        query = (
            select(Work)
            .outerjoin(composer, composer.id == Work.composer_entity_id)
            .where(
                or_(
                    Work.canonical_title.ilike(f"%{args.name}%"),
                    composer.label.ilike(f"%{args.name}%"),
                )
            )
            .order_by(Work.canonical_title)
            .limit(args.limit)
        )
        works = session.scalars(query).all()
        if not works:
            print(f"no work matching {args.name!r}")
            return 1
        for work in works:
            composer_label = work.composer.label if work.composer else "(unknown composer)"
            print(f"\n{work.canonical_title} — {composer_label}")
            features = _work_features_line(work)
            if features:
                print(f"  {features}")
            mentions = session.scalar(
                select(func.count(RawWorkMention.id)).where(RawWorkMention.work_id == work.id)
            )
            print(f"  mentions: {mentions}")
            aliases = session.scalars(
                select(WorkTitle.title).where(WorkTitle.work_id == work.id).distinct()
            ).all()
            for alias in aliases:
                if alias != work.canonical_title:
                    print(f"    alias: {alias}")
    return 0


def _add_alias(session: Session, work_id: uuid.UUID, title: str, source_id: int) -> None:
    title_key = normalize_title(title)
    exists = session.scalar(
        select(WorkTitle.id).where(
            WorkTitle.work_id == work_id,
            WorkTitle.title_key == title_key,
            WorkTitle.source_id == source_id,
        )
    )
    if exists is None:
        session.add(WorkTitle(work_id=work_id, title=title, title_key=title_key, source_id=source_id))


def cmd_review(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        if args.accept is not None:
            mention_id, work_id_raw = args.accept
            mention = session.get(RawWorkMention, int(mention_id))
            work = session.get(Work, uuid.UUID(work_id_raw))
            if mention is None or work is None:
                print("mention or work not found")
                return 1
            mention.work_id = work.id
            mention.match_status = "manual_matched"
            mention.match_method = "manual"
            _add_alias(session, work.id, mention.raw_title, mention.source_id)
            session.commit()
            print(f"matched mention #{mention.id} to {work.id} ({work.canonical_title})")
            return 0

        if args.new is not None:
            mention = session.get(RawWorkMention, args.new)
            if mention is None:
                print("mention not found")
                return 1
            work = new_work(
                mention.composer_entity_id, mention.raw_title, extract_features(mention.raw_title)
            )
            session.add(work)
            mention.work_id = work.id
            mention.match_status = "manual_matched"
            mention.match_method = "manual"
            _add_alias(session, work.id, mention.raw_title, mention.source_id)
            session.commit()
            print(f"created work {work.id} from mention #{mention.id}: {work.canonical_title}")
            return 0

        rows = session.scalars(
            select(RawWorkMention)
            .where(RawWorkMention.match_status == "needs_review")
            .order_by(RawWorkMention.match_score.desc())
            .limit(args.limit)
        ).all()
        if not rows:
            print("no mentions need review")
            return 0
        print("mentions needing review (resolve with --accept ID WORK_ID or --new ID):")
        for mention in rows:
            candidate = mention.candidate_work
            label = candidate.canonical_title if candidate is not None else "(no candidate)"
            score = mention.match_score if mention.match_score is not None else 0.0
            print(f"\n#{mention.id} [{score:.2f}] {mention.raw_composer or '?'} — {mention.raw_title}")
            print(f"     best candidate {mention.candidate_work_id}: {label}")
    return 0


def cmd_rematch(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        candidates: dict[uuid.UUID | None, list[Candidate]] = {}
        for work in session.scalars(select(Work)):
            candidates.setdefault(work.composer_entity_id, []).append(
                Candidate(work.id, extract_features(work.canonical_title))
            )

        pending = session.scalars(
            select(RawWorkMention).where(RawWorkMention.match_status.in_(["unmatched", "needs_review"]))
        ).all()
        for mention in pending:
            features = extract_features(mention.raw_title)
            result = resolve(features, candidates.get(mention.composer_entity_id, []))
            if result.status == "created":
                work = new_work(mention.composer_entity_id, mention.raw_title, features)
                session.add(work)
                candidates.setdefault(mention.composer_entity_id, []).append(Candidate(work.id, features))
                mention.work_id = work.id
            else:
                mention.work_id = result.work_id
            mention.match_status = result.status
            mention.match_score = result.score
            mention.match_method = result.method
            mention.candidate_work_id = result.candidate_work_id
            if mention.work_id is not None:
                _add_alias(session, mention.work_id, mention.raw_title, mention.source_id)
        session.commit()
        print(f"re-matched {len(pending)} mention(s)")
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

    p_works = sub.add_parser("works", help="search resolved works (by composer or title) and their aliases")
    p_works.add_argument("name", help="composer name or title substring")
    p_works.add_argument("--limit", type=int, default=20, help="max matching works to show")
    p_works.set_defaults(func=cmd_works)

    p_review = sub.add_parser("review", help="list (or resolve) work mentions the matcher flagged for review")
    p_review.add_argument("--limit", type=int, default=20, help="max mentions to list")
    p_review.add_argument(
        "--accept",
        nargs=2,
        metavar=("MENTION_ID", "WORK_ID"),
        help="match a mention to an existing work",
    )
    p_review.add_argument("--new", type=int, metavar="MENTION_ID", help="create a new work from a mention")
    p_review.set_defaults(func=cmd_review)

    p_rematch = sub.add_parser(
        "rematch", help="re-run matching over unmatched/needs-review mentions (after tuning)"
    )
    p_rematch.set_defaults(func=cmd_rematch)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
