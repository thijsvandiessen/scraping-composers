import argparse
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from ..db import get_engine, init_db
from ..ingestion import new_work
from ..models import Entity, RawWorkMention, Work, WorkTitle
from ..works import Candidate, extract_features, normalize_title, resolve


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
