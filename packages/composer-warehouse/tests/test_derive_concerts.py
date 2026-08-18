"""Tests for the silver concert-derivation pass."""

from composer_models import Concert, ConcertParticipant, ConcertWork, Entity
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.testing import FakeSource, ensemble, ingest_source, perf_mention, person
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


def _llm_raw(title_key: str) -> dict[str, object]:
    """The normalized, source-independent payload composer_extract writes."""
    return {
        "_source": "llm",
        "concert_key": "https://lso.co.uk/beethoven",
        "url": "https://lso.co.uk/beethoven",
        "date": "2024-05-01",
        "venue": "Barbican",
        "conductors": ["Simon Rattle"],
        "soloists": [{"name": "Janine Jansen", "discipline": "violin"}],
        "title": title_key,
    }


def test_derive_concerts_handles_llm_source(session: Session) -> None:
    lso = FakeSource(
        records=(
            perf_mention("https://lso.co.uk/beethoven#w0", "Symphony No. 5", "Beethoven", _llm_raw("sym5")),
            perf_mention("https://lso.co.uk/beethoven#w1", "Violin Concerto", "Brahms", _llm_raw("vc")),
            person("Simon Rattle", external_id="lso:rattle"),
            person("Janine Jansen", external_id="lso:jansen"),
        ),
        name="lso",
        base_url="https://lso.co.uk",
    )
    ingest_source(session, lso)

    stats = derive_concerts(session)

    concert = session.scalars(select(Concert)).one()  # both mentions grouped by concert_key
    assert concert.external_key == "https://lso.co.uk/beethoven"
    assert (concert.date, concert.venue) == ("2024-05-01", "Barbican")
    assert len(concert.works) == 2
    by_role = {(p.role, p.name): p for p in concert.participants}
    assert ("conductor", "Simon Rattle") in by_role
    assert by_role[("soloist", "Janine Jansen")].discipline == "violin"
    assert all(p.entity_id is not None for p in concert.participants)  # verbatim names resolve
    assert (stats.concerts, stats.participant_links) == (1, 2)


def test_derive_concerts_credits_the_orchestra(session: Session) -> None:
    """Berlinphil names the orchestra per work; it becomes an ensemble
    participant resolved against the ``ensemble`` entity of the same name."""
    berlinphil = FakeSource(
        records=(
            perf_mention(
                "perf:10-1",
                "Paradise and the Peri",
                "Robert Schumann",
                {
                    "concert_id": "10",
                    "date": "2009-02-08",
                    "conductors": ["Sir Simon Rattle"],
                    "ensembles": ["Berliner Philharmoniker"],
                },
            ),
            person("Rattle, Sir Simon", external_id="bp:rattle"),
            ensemble("Berliner Philharmoniker", external_id="bp:ens"),
        ),
        name="berlinphil",
        base_url="https://bp.example",
    )
    ingest_source(session, berlinphil)

    stats = derive_concerts(session)

    orchestra = session.scalars(select(Entity).where(Entity.label == "Berliner Philharmoniker")).one()
    participant = session.scalars(
        select(ConcertParticipant).where(ConcertParticipant.role == "ensemble")
    ).one()
    assert participant.name == "Berliner Philharmoniker"
    assert participant.entity_id == orchestra.id  # the ensemble entity, not a person
    assert stats.participant_links == 2  # conductor + orchestra


def test_derive_concerts_handles_rco_source(session: Session) -> None:
    """RCO reports concert-level credits on every work of the concert."""
    rco = FakeSource(
        records=(
            perf_mention(
                "perf:42:0",
                "Symphony No. 5",
                "Beethoven",
                {
                    "concert_id": 42,
                    "date": "2019-11-14T20:15:00Z",
                    "venue": "Het Concertgebouw",
                    "url": "https://rco.example/concert/42",
                    "conductor": "Daniele Gatti",
                    "soloists": [{"name": "Janine Jansen", "discipline": "violin"}],
                },
            ),
            perf_mention(
                "perf:42:1",
                "Egmont Overture",
                "Beethoven",
                {
                    "concert_id": 42,
                    "date": "2019-11-14T20:15:00Z",
                    "venue": "Het Concertgebouw",
                    "url": "https://rco.example/concert/42",
                    "conductor": "Daniele Gatti",
                    "soloists": [],
                },
            ),
            person("Daniele Gatti", external_id="rco:gatti"),
            person("Janine Jansen", external_id="rco:jansen"),
        ),
        name="rco",
        base_url="https://rco.example",
    )
    ingest_source(session, rco)

    stats = derive_concerts(session)

    concert = session.scalars(select(Concert)).one()  # both works grouped by concert id
    assert concert.external_key == "42"
    assert (concert.date, concert.venue) == ("2019-11-14", "Het Concertgebouw")
    assert len(concert.works) == 2
    assert {(p.role, p.name) for p in concert.participants} == {
        ("conductor", "Daniele Gatti"),
        ("soloist", "Janine Jansen"),
    }
    assert (stats.concerts, stats.participant_links) == (1, 2)


def test_derive_concerts_is_rerunnable(session: Session) -> None:
    _seed_performances(session)
    first = derive_concerts(session)
    second = derive_concerts(session)  # full rebuild: same result, no leftovers

    assert first == second
    assert session.scalar(select(func.count(Concert.id))) == 4
    assert session.scalar(select(func.count(ConcertWork.id))) == 5
    assert session.scalar(select(func.count(ConcertParticipant.id))) == 5


def test_work_profile_mentions_derive_no_concert(session: Session) -> None:
    """A work page states facts about a work, not a performance. Its mention
    shares the "llm" marker with concerts and is told apart by ``_kind``, so the
    guard has to match positively or every LA Phil work page becomes a concert."""
    laphil = FakeSource(
        records=(
            perf_mention(
                "https://www.laphil.com/works/violin-concerto-beethoven#work0",
                "Violin Concerto",
                "Ludwig van Beethoven",
                {
                    "_source": "llm",
                    "_kind": "work_profile",
                    "url": "https://www.laphil.com/works/violin-concerto-beethoven",
                    "title": "Violin Concerto",
                    "composer": "Ludwig van Beethoven",
                },
            ),
        ),
        name="laphil",
        base_url="https://www.laphil.com",
    )
    ingest_source(session, laphil)

    stats = derive_concerts(session)

    assert session.scalars(select(Concert)).all() == []
    assert stats.concerts == 0
