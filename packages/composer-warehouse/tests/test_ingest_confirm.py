"""``last_run_id`` only advances when a run completes (#195).

A run that dies partway has committed part of its work; the rows it touched
must not end up pointing at a run that is then marked ``failed``.
"""

from composer_config import settings
from composer_models import Base, EntityRecord, IngestRun, RawWorkMention
from composer_models.db import get_engine, init_db
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from composer_warehouse.ingestion import unconfirmed_counts
from composer_warehouse.testing import FakeSource, ingest_source, mention, person
from sqlalchemy import select, text
from sqlalchemy.orm import Session

HAYDN = person("Haydn, Joseph")
MOZART = person("Mozart, Wolfgang Amadeus")
BACH = person("Bach, Johann Sebastian")
FIFTH = mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1")
SIXTH = mention("Symphony No. 6, Op. 68", "Beethoven, Ludwig van", "m2")


def _last_run_ids(session: Session) -> dict[str, int]:
    ids: dict[str, int] = {}
    for model in (EntityRecord, RawWorkMention):
        for external_id, last_run_id in session.execute(select(model.external_id, model.last_run_id)):
            ids[external_id] = last_run_id
    return ids


def test_failed_run_partway_leaves_confirmed_rows_on_the_completed_run(session: Session) -> None:
    first = ingest_source(session, FakeSource(records=(HAYDN, MOZART, FIFTH)))
    first_seen = {r.external_id: r.last_seen_at for r in session.scalars(select(EntityRecord)).all()}
    # re-sights Haydn, Mozart and the Fifth, creates Bach and the Sixth, then dies
    failed = ingest_source(
        session, FakeSource(records=(HAYDN, MOZART, FIFTH, BACH, SIXTH, HAYDN), fail_after=5)
    )
    assert failed.status == "failed"
    assert failed.records_seen == 5

    ids = _last_run_ids(session)
    assert ids["Category:Haydn, Joseph"] == first.id
    assert ids["Category:Mozart, Wolfgang Amadeus"] == first.id
    assert ids["m1"] == first.id
    # created by the failed run: kept, and flagged by carrying its id
    assert ids["Category:Bach, Johann Sebastian"] == failed.id
    assert ids["m2"] == failed.id
    assert unconfirmed_counts(session) == {"fake": (1, 1)}

    # the failed run's sighting is still recorded, as a timestamp
    haydn = session.scalars(
        select(EntityRecord).where(EntityRecord.external_id == "Category:Haydn, Joseph")
    ).one()
    assert haydn.last_seen_at > first_seen["Category:Haydn, Joseph"]


def test_completed_run_confirms_rows_a_failed_run_created(session: Session) -> None:
    ingest_source(session, FakeSource(records=(HAYDN, BACH, FIFTH), fail_after=2))
    assert unconfirmed_counts(session) == {"fake": (2, 0)}

    completed = ingest_source(session, FakeSource(records=(HAYDN, BACH, FIFTH)))

    assert completed.status == "completed"
    assert set(_last_run_ids(session).values()) == {completed.id}
    assert unconfirmed_counts(session) == {}
    record = session.scalars(
        select(EntityRecord).where(EntityRecord.external_id == "Category:Bach, Johann Sebastian")
    ).one()
    assert record.first_run_id != completed.id  # first sighting still credits the failed run


def test_row_no_completed_run_sees_again_stays_flagged(session: Session) -> None:
    failed = ingest_source(session, FakeSource(records=(HAYDN, BACH), fail_after=1))
    completed = ingest_source(session, FakeSource(records=(BACH,)))

    ids = _last_run_ids(session)
    assert ids == {"Category:Haydn, Joseph": failed.id, "Category:Bach, Johann Sebastian": completed.id}
    assert unconfirmed_counts(session) == {"fake": (1, 0)}


def test_completed_run_confirms_only_its_own_source(session: Session) -> None:
    failed = ingest_source(session, FakeSource(records=(HAYDN, MOZART), name="a", fail_after=1))
    other = ingest_source(session, FakeSource(records=(HAYDN, MOZART), name="b"))

    by_source = session.execute(
        select(IngestRun.source_id, EntityRecord.last_run_id).join(
            IngestRun, IngestRun.id == EntityRecord.last_run_id
        )
    ).all()
    assert sorted(run_id for _, run_id in by_source) == [failed.id, other.id, other.id]
    assert unconfirmed_counts(session) == {"a": (1, 0)}


@requires_postgres
def test_confirmation_on_postgres(pg_url: str) -> None:  # noqa: F811 - fixture
    """The confirming UPDATE compares timestamptz on Postgres, text on SQLite."""
    admin = get_engine(pg_url, schema="public")
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{settings.silver_schema}"'))
    admin.dispose()
    engine = get_engine()
    Base.metadata.create_all(engine)
    try:
        with init_db(engine)() as session:
            failed = ingest_source(session, FakeSource(records=(HAYDN, BACH, FIFTH, SIXTH), fail_after=3))
            assert unconfirmed_counts(session) == {"fake": (2, 1)}
            completed = ingest_source(session, FakeSource(records=(HAYDN, FIFTH)))
            ids = _last_run_ids(session)
    finally:
        engine.dispose()
    assert ids == {
        "Category:Haydn, Joseph": completed.id,
        "Category:Bach, Johann Sebastian": failed.id,
        "m1": completed.id,
    }
