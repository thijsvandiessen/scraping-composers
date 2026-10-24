"""Tests for the place-linking pass: bare place labels onto pinned Wikidata places."""

from composer_models import Entity
from composer_schema import SourceClaim
from composer_warehouse.places import link_places
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy import select
from sqlalchemy.orm import Session


def citizen(country: str, qid: str | None = None) -> SourceClaim:
    return SourceClaim("citizen_of", "place", country, object_wikidata_id=qid)


def place(session: Session, key: str) -> Entity:
    entity = session.scalar(select(Entity).where(Entity.kind == "place", Entity.dedup_key == key))
    assert entity is not None
    return entity


def seed(session: Session, *bare: str, pinned: tuple[tuple[str, str], ...]) -> None:
    ingest_source(
        session,
        FakeSource(records=(person("A", *(citizen(c, q) for c, q in pinned)),), name="wikidata"),
    )
    ingest_source(session, FakeSource(records=(person("B", *(citizen(c) for c in bare)),), name="cmo"))


def test_a_bare_place_links_to_the_pinned_place_of_the_same_name(session: Session) -> None:
    seed(session, "Germany", pinned=(("Germany", "Q183"),))

    stats = link_places(session)

    assert stats.linked == 1
    assert place(session, "germany").canonical_entity_id == place(session, "germany|Q183").id
    assert place(session, "germany|Q183").canonical_entity_id is None


def test_a_name_two_pinned_places_share_stays_unlinked(session: Session) -> None:
    seed(session, "Georgia", pinned=(("Georgia", "Q230"), ("Georgia", "Q1428")))

    stats = link_places(session)

    assert stats.ambiguous == 1
    assert place(session, "georgia").canonical_entity_id is None


def test_a_bare_place_with_no_pinned_twin_is_left_alone(session: Session) -> None:
    seed(session, "Bohemia", pinned=(("Germany", "Q183"),))

    assert link_places(session).unmatched == 1
    assert place(session, "bohemia").canonical_entity_id is None


def test_rerunning_drops_a_link_that_became_ambiguous(session: Session) -> None:
    seed(session, "Georgia", pinned=(("Georgia", "Q230"),))
    link_places(session)
    assert place(session, "georgia").canonical_entity_id is not None

    seed(session, pinned=(("Georgia", "Q1428"),))
    link_places(session)

    assert place(session, "georgia").canonical_entity_id is None
