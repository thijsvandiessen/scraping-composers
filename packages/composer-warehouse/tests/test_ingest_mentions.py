"""Ingest tests for work mentions: resolution, dedup, idempotency."""

import json

from composer_models import Entity, RawWorkMention, Work, WorkTitle
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention, person
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def test_mention_creates_work_alias_and_mention(session: Session) -> None:
    run = ingest_source(
        session,
        FakeSource(records=(mention("Symphony No. 5 in C minor, Op. 67", "Beethoven, Ludwig van"),)),
    )
    assert run.records_new == 1

    work = session.scalars(select(Work)).one()
    assert work.canonical_title == "Symphony No. 5 in C minor, Op. 67"
    assert work.opus_number == "67"
    assert work.number == 5
    assert work.composer is not None and work.composer.label == "Beethoven, Ludwig van"

    row = session.scalars(select(RawWorkMention)).one()
    assert row.work_id == work.id
    assert row.match_status == "created"

    alias = session.scalars(select(WorkTitle)).one()
    assert alias.work_id == work.id
    assert alias.title == "Symphony No. 5 in C minor, Op. 67"


def test_same_composer_and_work_matches_existing(session: Session) -> None:
    # different formatting of the same composer + same opus -> one work
    ingest_source(
        session,
        FakeSource(
            records=(
                mention("Symphony No. 5 in C minor, Op. 67", "Beethoven, Ludwig van", "m1"),
                mention("Sinfonie Nr. 5 c-moll, op. 67", "Ludwig van Beethoven", "m2"),
            )
        ),
    )
    works = session.scalars(select(Work)).all()
    assert len(works) == 1

    rows = session.scalars(select(RawWorkMention).order_by(RawWorkMention.external_id)).all()
    assert [r.match_status for r in rows] == ["created", "auto_matched"]
    assert rows[1].work_id == works[0].id

    aliases = {t.title for t in session.scalars(select(WorkTitle))}
    assert aliases == {"Symphony No. 5 in C minor, Op. 67", "Sinfonie Nr. 5 c-moll, op. 67"}


def test_same_title_different_composer_creates_two_works(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(
            records=(
                mention("Symphony No. 1", "Brahms, Johannes", "m1"),
                mention("Symphony No. 1", "Mahler, Gustav", "m2"),
            )
        ),
    )
    assert session.scalar(select(func.count(Work.id))) == 2


def test_composer_resolves_to_person_entity_shared_with_people_source(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(
            records=(
                person("Beethoven, Ludwig van"),
                mention("Symphony No. 5, Op. 67", "Ludwig van Beethoven", "m1"),
            )
        ),
    )
    people = session.scalars(select(Entity).where(Entity.kind == "person")).all()
    assert len(people) == 1  # the people record and the mention's composer are one entity
    work = session.scalars(select(Work)).one()
    assert work.composer_entity_id == people[0].id


def test_reingest_is_idempotent(session: Session) -> None:
    source = FakeSource(records=(mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van"),))
    first = ingest_source(session, source)
    second = ingest_source(session, source)

    assert (first.records_new, second.records_new) == (1, 0)
    assert session.scalar(select(func.count(Work.id))) == 1
    assert session.scalar(select(func.count(RawWorkMention.id))) == 1
    assert session.scalar(select(func.count(WorkTitle.id))) == 1

    row = session.scalars(select(RawWorkMention)).one()
    assert row.first_run_id == first.id
    assert row.last_run_id == second.id


def test_reingest_with_changed_content_updates_mention(session: Session) -> None:
    """A re-sighted mention whose content genuinely changed must update the
    stored row (issue #137) *and* get re-matched against the work catalogue
    (issue #142) — not just have its raw fields updated while the stale match
    decision from the original title stands."""
    first = ingest_source(
        session,
        FakeSource(records=(mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),)),
    )
    second = ingest_source(
        session,
        FakeSource(
            records=(
                perf_mention(
                    "m1",
                    "Symphony No. 5 in C minor, Op. 67 (corrected)",
                    "Beethoven, Ludwig van",
                    {"note": "corrected"},
                ),
            )
        ),
    )

    assert (first.records_new, second.records_new) == (1, 0)

    row = session.scalars(select(RawWorkMention)).one()
    assert row.raw_title == "Symphony No. 5 in C minor, Op. 67 (corrected)"
    assert json.loads(row.raw) == {"note": "corrected"}
    assert row.first_run_id == first.id
    assert row.last_run_id == second.id
    # re-derived (not stale): the corrected title shares the original work's
    # opus number, so it now auto-matches back to that same work rather than
    # standing on the "created" decision made for the original (pre-correction)
    # title
    work = session.scalars(select(Work)).one()
    assert row.match_status == "auto_matched"
    assert row.work_id == work.id


def test_reingest_with_unchanged_content_leaves_row_untouched(session: Session) -> None:
    source = FakeSource(records=(mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),))
    ingest_source(session, source)
    ingest_source(session, source)

    row = session.scalars(select(RawWorkMention)).one()
    assert row.raw_title == "Symphony No. 5, Op. 67"
    assert session.scalar(select(func.count(RawWorkMention.id))) == 1
    # unchanged content shouldn't re-run matching or add a duplicate alias
    assert session.scalar(select(func.count(WorkTitle.id))) == 1


def test_reingest_with_corrected_title_rematches_to_different_work(session: Session) -> None:
    """A re-sighted mention's corrected title can now match a work it
    originally didn't — the re-sighted path must re-run resolve() against the
    full catalogue, not stand on the original (pre-correction) decision
    (issue #142)."""
    ingest_source(
        session,
        FakeSource(
            records=(
                mention("Symphony No. 5, Op. 67", "Brahms, Johannes", "m1"),
                mention("Symphony No. 1, Op. 68", "Brahms, Johannes", "m2"),
            )
        ),
    )
    works = {w.canonical_title: w for w in session.scalars(select(Work)).all()}
    assert len(works) == 2
    work_b = works["Symphony No. 1, Op. 68"]

    ingest_source(
        session,
        FakeSource(
            records=(
                perf_mention(
                    "m1",
                    "Symphony No. 1, Op. 68 (misattributed source, corrected)",
                    "Brahms, Johannes",
                    {"v": 2},
                ),
            )
        ),
    )

    row = session.scalars(select(RawWorkMention).where(RawWorkMention.external_id == "m1")).one()
    assert row.match_status == "auto_matched"
    assert row.match_method == "opus"
    assert row.work_id == work_b.id

    # the work m1 originally created is now referenced by no mention, but is
    # deliberately left in place with its alias intact — deduping works is the
    # dedupe pass's job, not the ingest loop's (see resolve_mention)
    work_a = works["Symphony No. 5, Op. 67"]
    assert session.get(Work, work_a.id) is not None
    assert (
        session.scalar(select(func.count(RawWorkMention.id)).where(RawWorkMention.work_id == work_a.id)) == 0
    )
    orphan_aliases = {
        t.title for t in session.scalars(select(WorkTitle).where(WorkTitle.work_id == work_a.id))
    }
    assert orphan_aliases == {"Symphony No. 5, Op. 67"}


def test_reingest_with_changed_content_adds_new_alias(session: Session) -> None:
    """A re-sighted mention's corrected title is recorded as a new alias
    (issue #142); the original title's alias still coexists."""
    ingest_source(
        session,
        FakeSource(records=(mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),)),
    )
    ingest_source(
        session,
        FakeSource(
            records=(
                perf_mention(
                    "m1", "Symphony No. 5 in C minor, Op. 67 (corrected)", "Beethoven, Ludwig van", {"v": 2}
                ),
            )
        ),
    )

    aliases = {t.title for t in session.scalars(select(WorkTitle))}
    assert aliases == {"Symphony No. 5, Op. 67", "Symphony No. 5 in C minor, Op. 67 (corrected)"}


def test_reingest_with_corrected_composer_updates_composer_entity(session: Session) -> None:
    """A re-sighted mention's corrected composer text must be re-resolved to
    an entity, and the mention's ``composer_entity_id`` refreshed to match —
    not left pointing at the original (pre-correction) composer (issue #142)."""
    ingest_source(session, FakeSource(records=(mention("Requiem", "Mozart, Wolfgang", "m1"),)))
    ingest_source(
        session,
        FakeSource(records=(perf_mention("m1", "Requiem", "Salieri, Antonio", {"v": 2}),)),
    )

    salieri = session.scalars(select(Entity).where(Entity.label == "Salieri, Antonio")).one()
    row = session.scalars(select(RawWorkMention)).one()
    assert row.composer_entity_id == salieri.id


def test_batch_commit_with_mentions(session: Session) -> None:
    # COMMIT_BATCH=1000: distinct numbers -> distinct works, exercising the
    # mid-run commit path while resolving mentions.
    records = tuple(mention(f"Etude No. {i}", "Chopin, Frederic", f"m{i}") for i in range(1001))
    run = ingest_source(session, FakeSource(records=records))

    assert run.status == "completed"
    assert run.records_seen == 1001
    assert session.scalar(select(func.count(Work.id))) == 1001


def test_failing_source_preserves_committed_mentions(session: Session) -> None:
    source = FakeSource(
        records=(
            mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),
            mention("Symphony No. 6, Op. 68", "Beethoven, Ludwig van", "m2"),
        ),
        fail_after=1,
    )
    run = ingest_source(session, source)

    assert run.status == "failed"
    # the mention processed before the error is preserved
    assert session.scalar(select(func.count(Work.id))) == 1
    assert session.scalar(select(func.count(RawWorkMention.id))) == 1
