import argparse

from sqlalchemy import select

from ..db import get_engine, init_db
from ..models import PersonMatch
from ..persons import dedupe_persons


def cmd_dedupe_persons(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        auto, review = dedupe_persons(session)
    print(f"auto-linked {auto} duplicate(s), {review} pair(s) need review")
    return 0


def cmd_person_review(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        if args.accept is not None or args.reject is not None:
            match_id = args.accept if args.accept is not None else args.reject
            match = session.get(PersonMatch, match_id)
            if match is None or match.status != "needs_review":
                print("no pending match with that id")
                return 1
            if args.accept is not None:
                match.status = "accepted"
                match.entity.canonical_entity_id = match.canonical_entity_id
                print(f"linked {match.entity.label!r} -> {match.canonical.label!r}")
            else:
                match.status = "rejected"
                print(f"rejected match #{match.id}")
            session.commit()
            return 0

        rows = session.scalars(
            select(PersonMatch)
            .where(PersonMatch.status == "needs_review")
            .order_by(PersonMatch.score.desc())
            .limit(args.limit)
        ).all()
        if not rows:
            print("no person matches need review")
            return 0
        print("person matches needing review (resolve with --accept ID or --reject ID):")
        for match in rows:
            print(
                f"\n#{match.id} [{match.score:.2f} {match.method}]"
                f" {match.entity.label!r} -> {match.canonical.label!r}"
            )
    return 0
