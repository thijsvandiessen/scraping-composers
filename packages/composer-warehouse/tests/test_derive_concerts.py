"""Tests for the silver concert-derivation pass."""

from composer_warehouse.concerts import derive_concerts
from composer_warehouse.models import Concert, ConcertParticipant, ConcertWork, Entity
from composer_warehouse.testing import FakeSource, ingest_source, perf_mention, person
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _seed_performances(session: Session) -> None:
    """Performance payloads in each source's real shape."""
    concertgebouw = FakeSource(
        records=(
            perf_mention(
                "perf:0",
                "Symfonie nr. 5",
                "Beethoven, Ludwig van",
                {"date": "30-06-1929", "city": "Amsterdam", "conductor": "Beinum, Eduard van"},
            ),
            perf_mention(
                "perf:1",
                "Egmont Ouverture",
                "Beethoven, Ludwig van",
                {"date": "30-06-1929", "city": "Amsterdam", "conductor": "Beinum, Eduard van"},
            ),
            perf_mention(
                "perf:2",
                "Vioolconcert",
                "Beethoven, Ludwig van",
                {"date": "01-07-1929", "city": "Amsterdam", "conductor": "Mengelberg, Willem"},
            ),
            person("Beinum, Eduard van", external_id="cg:beinum"),
            person("Mengelberg, Willem", external_id="cg:mengelberg"),
        ),
        name="concertgebouw_archive",
        base_url="https://cg.example",
    )
    berlinphil = FakeSource(
        records=(
            perf_mention(
                "perf:10-1",
                "Paradise and the Peri",
                "Robert Schumann",
                {
                    "concert_id": "10",
                    "date": "2009-02-08",
                    "season": "2008/09",
                    "url": "https://www.digitalconcerthall.com/en/concert/10",
                    "conductors": ["Sir Simon Rattle"],  # natural order; entity is surname-first
                    "soloists": [{"name": "Sally Matthews", "discipline": "soprano"}],
                },
            ),
            person("Rattle, Sir Simon", external_id="bp:rattle"),
            person("Matthews, Sally", external_id="bp:matthews"),
        ),
        name="berlinphil",
        base_url="https://bp.example",
    )
    nyphil = FakeSource(
        records=(
            perf_mention(
                "perf:100:0:0",
                "Luisa Miller",
                "Verdi, Giuseppe",
                {
                    "programID": "100",
                    "date": "1993-06-26",
                    "season": "1992-93",
                    "eventType": "Parks",
                    "venue": "Great Lawn",
                    "location": "Manhattan, NY",
                    "conductors": ["Ghost, Unresolvable"],  # no matching person entity
                },
            ),
        ),
        name="nyphil",
        base_url="https://nyp.example",
    )
    for source in (concertgebouw, berlinphil, nyphil):
        ingest_source(session, source)


def test_derive_concerts_groups_and_resolves(session: Session) -> None:
    _seed_performances(session)
    stats = derive_concerts(session)

    concerts = session.scalars(select(Concert).order_by(Concert.date)).all()
    # cg: two dates -> two concerts; bp: one; nyphil: one
    assert len(concerts) == 4
    first = concerts[0]
    assert first.date == "1929-06-30"  # DD-MM-YYYY normalized to ISO
    assert first.venue == "Amsterdam"
    assert len(first.works) == 2  # both mentions of that evening grouped
    assert {p.name for p in first.participants} == {"Beinum, Eduard van"}

    beinum = session.scalars(select(Entity).where(Entity.label == "Beinum, Eduard van")).one()
    assert first.participants[0].entity_id == beinum.id

    # natural-order conductor name resolves to the surname-first entity
    rattle_concert = next(c for c in concerts if c.url is not None)
    rattle = session.scalars(select(Entity).where(Entity.label == "Rattle, Sir Simon")).one()
    by_role = {p.role: p for p in rattle_concert.participants}
    assert by_role["conductor"].entity_id == rattle.id
    assert rattle_concert.season == "2008/09"

    # soloists are participants too, with their discipline and entity link
    matthews = session.scalars(select(Entity).where(Entity.label == "Matthews, Sally")).one()
    assert by_role["soloist"].name == "Sally Matthews"
    assert by_role["soloist"].discipline == "soprano"
    assert by_role["soloist"].entity_id == matthews.id

    # unresolved conductor keeps the row, without an entity link
    nyp = next(c for c in concerts if c.venue == "Great Lawn, Manhattan, NY")
    assert nyp.participants[0].name == "Ghost, Unresolvable"
    assert nyp.participants[0].entity_id is None
    assert (nyp.season, nyp.event_type) == ("1992-93", "Parks")

    assert stats.concerts == 4
    assert stats.participant_links == 4  # Beinum, Mengelberg, Rattle, Matthews
    assert stats.unresolved_participant_names == 1


def test_derive_concerts_is_rerunnable(session: Session) -> None:
    _seed_performances(session)
    first = derive_concerts(session)
    second = derive_concerts(session)  # full rebuild: same result, no leftovers

    assert first == second
    assert session.scalar(select(func.count(Concert.id))) == 4
    assert session.scalar(select(func.count(ConcertWork.id))) == 5
    assert session.scalar(select(func.count(ConcertParticipant.id))) == 5
