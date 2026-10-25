import argparse
import json
import uuid
from dataclasses import dataclass

from composer_models import Entity, RawWorkMention, Source, Work, WorkIdentifier, WorkLink, WorkTitle
from composer_models.db import get_engine, init_db
from composer_warehouse.ingestion import new_work
from composer_warehouse.works import (
    EXTERNAL_SCHEMES,
    Candidate,
    add_alias,
    add_identifiers,
    extract_features,
    load_candidates,
    mention_features,
    refresh_candidate,
    resolve,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased


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
            add_alias(session, work.id, mention.raw_title, mention.source_id)
            session.commit()
            print(f"matched mention #{mention.id} to {work.id} ({work.canonical_title})")
            return 0

        if args.new is not None:
            mention = session.get(RawWorkMention, args.new)
            if mention is None:
                print("mention not found")
                return 1
            work = new_work(
                mention.composer_entity_id,
                mention.raw_title,
                extract_features(mention.raw_title, composer=mention.raw_composer),
            )
            session.add(work)
            mention.work_id = work.id
            mention.match_status = "manual_matched"
            mention.match_method = "manual"
            add_alias(session, work.id, mention.raw_title, mention.source_id)
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
        candidates = load_candidates(session)
        pending = session.scalars(
            select(RawWorkMention).where(RawWorkMention.match_status.in_(["unmatched", "needs_review"]))
        ).all()
        for mention in pending:
            features = mention_features(
                mention.source.name, mention.raw_title, mention.raw_composer, json.loads(mention.raw)
            )
            composer_candidates = candidates.setdefault(mention.composer_entity_id, [])
            result = resolve(features, composer_candidates)
            if result.status == "created":
                work = new_work(mention.composer_entity_id, mention.raw_title, features)
                session.add(work)
                composer_candidates.append(Candidate(work.id, features))
                mention.work_id = work.id
            else:
                mention.work_id = result.work_id
            mention.match_status = result.status
            mention.match_score = result.score
            mention.match_method = result.method
            mention.candidate_work_id = result.candidate_work_id
            work_id = mention.work_id
            if work_id is not None:
                add_alias(session, work_id, mention.raw_title, mention.source_id)
                add_identifiers(session, work_id, features.identifiers, mention.source_id)
                refresh_candidate(composer_candidates, work_id, features.identifiers)
        session.commit()
        print(f"re-matched {len(pending)} mention(s)")
    return 0


@dataclass(frozen=True)
class IdentityRow:
    """How many of one source's works the strong keys reach."""

    source: str
    mentions: int
    works: int
    with_catalogue: int
    with_external: int
    with_either: int
    derived_links: int

    def share(self, count: int) -> float:
        return 100.0 * count / self.works if self.works else 0.0


def work_identity(session: Session) -> list[IdentityRow]:
    """Per source (plus an "(all)" row): its mentions, the works they resolved
    to, and how many of those works carry a catalogue number, an external id,
    either, and a link to a parent work. A work's identifiers count whichever
    source stated them -- the point is how many works are anchored."""
    catalogue: set[uuid.UUID] = set()
    external: set[uuid.UUID] = set()
    for work_id, scheme in session.execute(select(WorkIdentifier.work_id, WorkIdentifier.scheme).distinct()):
        (external if scheme in EXTERNAL_SCHEMES else catalogue).add(work_id)
    derived = set(session.scalars(select(WorkLink.work_id).distinct()))

    mentions: dict[str, int] = {}
    works: dict[str, set[uuid.UUID]] = {}
    for source, work_id in session.execute(
        select(Source.name, RawWorkMention.work_id).join(Source, Source.id == RawWorkMention.source_id)
    ):
        mentions[source] = mentions.get(source, 0) + 1
        if work_id is not None:
            works.setdefault(source, set()).add(work_id)
    all_works = set(session.scalars(select(Work.id)))

    def row(source: str, mention_count: int, ids: set[uuid.UUID]) -> IdentityRow:
        return IdentityRow(
            source=source,
            mentions=mention_count,
            works=len(ids),
            with_catalogue=len(ids & catalogue),
            with_external=len(ids & external),
            with_either=len(ids & (catalogue | external)),
            derived_links=len(ids & derived),
        )

    rows = [row(source, mentions[source], works.get(source, set())) for source in sorted(mentions)]
    rows.append(row("(all)", sum(mentions.values()), all_works))
    return rows


def cmd_work_identity(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        rows = work_identity(session)
    header = ("mentions", "works", "catalogue", "external", "either", "derived")
    print(f"{'source':<24} " + " ".join(f"{h:>9}" for h in header))
    for r in rows:
        print(
            f"{r.source:<24} {r.mentions:>9} {r.works:>9} {r.share(r.with_catalogue):>8.1f}% "
            f"{r.share(r.with_external):>8.1f}% {r.share(r.with_either):>8.1f}% {r.derived_links:>9}"
        )
    return 0
