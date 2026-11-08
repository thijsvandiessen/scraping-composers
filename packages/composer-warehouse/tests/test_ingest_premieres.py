"""Ingest tests for carrying a mention's world premiere onto its work."""

from typing import Any

from composer_models import Source, Work
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention
from sqlalchemy import select
from sqlalchemy.orm import Session

NINTH = "Symphony No. 9 in D minor, Op. 125"
BEETHOVEN = "Beethoven, Ludwig van"


def premiere(date: str, place: str | None = None) -> dict[str, Any]:
    return {"premiere": {"date": date, "place": place}}


def ingest(session: Session, source: str, *mentions: Any) -> None:
    ingest_source(session, FakeSource(records=mentions, name=source, base_url=f"https://{source}.example"))


def source_id(session: Session, name: str) -> int:
    return session.scalars(select(Source.id).where(Source.name == name)).one()


def test_a_mention_with_a_premiere_sets_it_on_the_new_work(session: Session) -> None:
    ingest(session, "wikidata_works", perf_mention("w1", NINTH, BEETHOVEN, premiere("1824-05-07", "Vienna")))

    work = session.scalars(select(Work)).one()
    assert (work.premiere_date, work.premiere_place) == ("1824-05-07", "Vienna")
    assert work.premiere_source_id == source_id(session, "wikidata_works")


def test_a_later_source_backfills_a_work_without_a_premiere(session: Session) -> None:
    ingest(session, "openopus", mention(NINTH, BEETHOVEN))
    assert session.scalars(select(Work)).one().premiere_date is None

    ingest(session, "wikidata_works", perf_mention("w1", NINTH, BEETHOVEN, premiere("1824-05-07", "Vienna")))
    work = session.scalars(select(Work)).one()
    assert work.premiere_date == "1824-05-07"
    assert work.premiere_source_id == source_id(session, "wikidata_works")


def test_an_earlier_date_from_another_source_wins(session: Session) -> None:
    ingest(session, "roh", perf_mention("r1", NINTH, BEETHOVEN, premiere("1825-03-21", "London")))
    ingest(session, "wikidata_works", perf_mention("w1", NINTH, BEETHOVEN, premiere("1824-05-07", "Vienna")))

    work = session.scalars(select(Work)).one()
    assert (work.premiere_date, work.premiere_place) == ("1824-05-07", "Vienna")
    assert work.premiere_source_id == source_id(session, "wikidata_works")


def test_a_later_date_does_not_displace_an_earlier_one(session: Session) -> None:
    ingest(session, "wikidata_works", perf_mention("w1", NINTH, BEETHOVEN, premiere("1824-05-07", "Vienna")))
    ingest(session, "roh", perf_mention("r1", NINTH, BEETHOVEN, premiere("1825-03-21", "London")))

    work = session.scalars(select(Work)).one()
    assert (work.premiere_date, work.premiere_place) == ("1824-05-07", "Vienna")
    assert work.premiere_source_id == source_id(session, "wikidata_works")


def test_a_full_date_refines_a_year_of_the_same_premiere(session: Session) -> None:
    ingest(session, "a", perf_mention("a1", NINTH, BEETHOVEN, premiere("1824")))
    ingest(session, "b", perf_mention("b1", NINTH, BEETHOVEN, premiere("1824-05-07")))
    assert session.scalars(select(Work)).one().premiere_date == "1824-05-07"


def test_two_mentions_in_one_run_settle_on_the_earliest(session: Session) -> None:
    ingest(
        session,
        "wikidata_works",
        perf_mention("w1", NINTH, BEETHOVEN, premiere("1830")),
        perf_mention("w2", "Sinfonie Nr. 9 d-moll, op. 125", BEETHOVEN, premiere("1824-05-07")),
        perf_mention("w3", NINTH, BEETHOVEN, premiere("1826")),
    )
    assert session.scalars(select(Work)).one().premiere_date == "1824-05-07"


def test_a_corrected_mention_carries_its_new_premiere(session: Session) -> None:
    ingest(session, "wikidata_works", perf_mention("w1", NINTH, BEETHOVEN, {}))
    ingest(session, "wikidata_works", perf_mention("w1", NINTH, BEETHOVEN, premiere("1824-05-07")))
    assert session.scalars(select(Work)).one().premiere_date == "1824-05-07"


def test_mentions_without_the_convention_leave_the_premiere_alone(session: Session) -> None:
    ingest(
        session,
        "roh",
        perf_mention("r1", NINTH, BEETHOVEN, {"premieres": {"ROH premiere": {"date": "1825-03-21"}}}),
        perf_mention("r2", NINTH, BEETHOVEN, premiere("21 March 1825")),
    )
    work = session.scalars(select(Work)).one()
    assert (work.premiere_date, work.premiere_place, work.premiere_source_id) == (None, None, None)
