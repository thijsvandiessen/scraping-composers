"""Catalogue evidence in the person dedupe (#362): two entities holding the same
number in one composer's catalogue are linked, conservatively."""

import uuid

from composer_models import Entity, PersonMatch
from composer_schema import SourceClaim
from composer_warehouse.persons import dedupe_persons, parse_name
from composer_warehouse.persons.catalogues import catalogue_links, compatible
from composer_warehouse.persons.corpus import PersonRecord
from composer_warehouse.testing import FakeSource, ingest_source, mention, person
from sqlalchemy import select
from sqlalchemy.orm import Session

WOLFGANG = "Mozart, Wolfgang Amadeus"
LEOPOLD = "Mozart, Leopold"


def _ingest(session: Session, *records: object) -> None:
    filler = [person(f"Smith, Filler{i}") for i in range(40)]
    ingest_source(session, FakeSource(records=(*records, *filler)))  # pyright: ignore[reportArgumentType]


def _canonical(session: Session) -> dict[str, str | None]:
    people = {e.id: e for e in session.scalars(select(Entity).where(Entity.kind == "person"))}
    return {
        e.label: (people[e.canonical_entity_id].label if e.canonical_entity_id else None)
        for e in people.values()
        if not e.label.startswith("Smith")
    }


def _symphonies(composer: str, *numbers: int) -> list[object]:
    return [mention(f"Symphony KV {n}", composer, f"{composer}:{n}") for n in numbers]


def test_a_surname_only_entity_links_to_the_composer_whose_numbers_it_holds(session: Session) -> None:
    _ingest(session, *_symphonies(WOLFGANG, 550, 551), *_symphonies("Mozart", 550))
    result = dedupe_persons(session)

    assert (result.auto, result.catalogue) == (1, 1)
    assert _canonical(session)["Mozart"] == WOLFGANG
    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("auto_linked", "shared_catalogue:1")
    assert dedupe_persons(session).catalogue == 0  # decided once


def test_conflicting_given_names_are_two_people_whatever_they_share(session: Session) -> None:
    # Leopold "holding" a Köchel number is a misattribution, not his son.
    _ingest(session, *_symphonies(WOLFGANG, 550), *_symphonies(LEOPOLD, 550))
    assert dedupe_persons(session).catalogue == 0
    assert _canonical(session)[LEOPOLD] is None


def test_different_surnames_are_never_linked(session: Session) -> None:
    _ingest(session, *_symphonies(WOLFGANG, 550), *_symphonies("Hummel, Johann Nepomuk", 550))
    assert dedupe_persons(session).catalogue == 0


def test_a_surname_only_entity_between_two_rivals_links_to_neither(session: Session) -> None:
    _ingest(
        session,
        *_symphonies(WOLFGANG, 550, 551),
        *_symphonies(LEOPOLD, 552),
        *_symphonies("Mozart", 550, 552),
    )
    assert dedupe_persons(session).catalogue == 0
    assert set(_canonical(session).values()) == {None}


def test_a_dominant_partner_wins_over_a_weak_rival(session: Session) -> None:
    _ingest(
        session,
        *_symphonies(WOLFGANG, 550, 551, 543),
        *_symphonies(LEOPOLD, 552),
        *_symphonies("Mozart", 550, 551, 543, 552),
    )
    assert dedupe_persons(session).catalogue == 1
    assert _canonical(session) == {WOLFGANG: None, LEOPOLD: None, "Mozart": WOLFGANG}


def test_years_a_generation_apart_are_never_linked(session: Session) -> None:
    _ingest(
        session,
        person("Bach", SourceClaim("born_on", value="1735")),
        person("Bach, Johann Sebastian", SourceClaim("born_on", value="1685")),
        mention("Prelude BWV 846", "Bach", "b1"),
        mention("Prelude in C major, BWV 846", "Bach, Johann Sebastian", "b2"),
    )
    assert dedupe_persons(session).catalogue == 0


def test_a_number_every_composer_restarts_is_no_evidence(session: Session) -> None:
    _ingest(
        session,
        mention("Variations WoO 80", "Beethoven", "a1"),
        mention("Variations WoO 80", "Beethoven, Ludwig van", "a2"),
    )
    assert dedupe_persons(session).catalogue == 0


def test_a_pair_queued_for_review_is_upgraded_by_a_shared_number(session: Session) -> None:
    born = SourceClaim("born_on", value="1732")
    _ingest(
        session,
        person("Haydn", born),
        person("Haydn, Joseph", born),
        mention("Symphony No. 94, Hob. I:94", "Haydn", "h1"),
        mention("Symphony No. 94 in G major, Hob. I:94", "Haydn, Joseph", "h2"),
    )
    result = dedupe_persons(session)

    assert (result.auto, result.review, result.catalogue) == (1, 0, 1)
    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("auto_linked", "shared_catalogue:1")
    assert _canonical(session)["Haydn"] == "Haydn, Joseph"


def _record(label: str, born: int | None = None) -> PersonRecord:
    return PersonRecord(entity_id=uuid.uuid4(), label=label, name=parse_name(label), birth_year=born)


def test_compatible_weighs_every_pair_across_two_clusters() -> None:
    haydn = [_record("Joseph Haydn"), _record("Haydn, Franz Joseph", 1732)]
    assert compatible([_record("Haydn")], haydn)
    assert not compatible([_record("Haydn, Michael")], haydn)
    assert not compatible([_record("Haydn", 1737)], haydn)
    assert not compatible([_record("Hoffstetter, Roman")], haydn)


def test_links_join_the_biggest_holders_and_respect_cannot_links() -> None:
    wolfgang, alias, short = _record(WOLFGANG), _record("W. A. Mozart"), _record("Mozart")
    keys = {
        wolfgang.entity_id: {("KV", "550"), ("KV", "551")},
        alias.entity_id: {("KV", "1")},
        short.entity_id: {("KV", "550")},
    }
    records = [wolfgang, alias, short]
    [(a, b, shared)] = catalogue_links(records, keys)
    assert ({a, b}, shared) == ({wolfgang.entity_id, short.entity_id}, 1)
    assert catalogue_links(records, keys, cannot_link=[(short.entity_id, wolfgang.entity_id)]) == []
