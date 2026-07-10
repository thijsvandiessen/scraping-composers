"""Tests for the post-hoc person dedupe pass."""

from composer_schema import SourceClaim
from composer_warehouse.models import Entity, PersonMatch
from composer_warehouse.persons import dedupe_persons
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _ingest(session: Session, *people: object) -> None:
    ingest_source(session, FakeSource(records=people))  # pyright: ignore[reportArgumentType]


def _by_label(session: Session) -> dict[str, Entity]:
    return {e.label: e for e in session.scalars(select(Entity).where(Entity.kind == "person"))}


def test_initials_pair_is_auto_linked(session: Session) -> None:
    _ingest(session, person("Bach, J.S."), person("Bach, Johann Sebastian"))
    auto, review = dedupe_persons(session)

    assert (auto, review) == (1, 0)
    people = _by_label(session)
    dup, canonical = people["Bach, J.S."], people["Bach, Johann Sebastian"]
    assert dup.canonical_entity_id == canonical.id  # fuller name is canonical
    assert canonical.canonical_entity_id is None

    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("auto_linked", "initials")


def test_surname_only_is_queued_for_review_not_linked(session: Session) -> None:
    _ingest(session, person("Beethoven"), person("Beethoven, Ludwig van"))
    auto, review = dedupe_persons(session)

    assert (auto, review) == (0, 1)
    assert session.scalar(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None))) == 0
    match = session.scalars(select(PersonMatch)).one()
    assert match.status == "needs_review"
    assert match.entity.label == "Beethoven"  # the sparser name is the duplicate


def test_birth_year_conflict_keeps_namesakes_separate(session: Session) -> None:
    # "Strauss, J." and "Strauss, Johann" would auto-link on initials alone, but
    # a century between their birth years marks them as different people.
    _ingest(
        session,
        person("Strauss, J.", SourceClaim("born_on", value="1804")),
        person("Strauss, Johann", SourceClaim("born_on", value="1825")),
    )
    auto, review = dedupe_persons(session)
    assert (auto, review) == (0, 0)
    assert session.scalar(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None))) == 0


def test_reingest_pass_is_idempotent(session: Session) -> None:
    _ingest(session, person("Bach, J.S."), person("Bach, Johann Sebastian"))
    first = dedupe_persons(session)
    second = dedupe_persons(session)

    assert first == (1, 0)
    assert second == (0, 0)  # the decided pair is skipped on re-run
    assert session.scalar(select(func.count(PersonMatch.id))) == 1


def test_different_surnames_are_not_compared(session: Session) -> None:
    _ingest(session, person("Bach, Johann Sebastian"), person("Handel, George Frideric"))
    assert dedupe_persons(session) == (0, 0)


def test_aliases_are_used_for_matching(session: Session) -> None:
    # "Strauss, Johann" and "Strauss, J." (son)
    # But let's test a completely different name using alias!
    _ingest(
        session,
        person("Beethoven, Ludwig van", SourceClaim("also_known_as", value="Louis van Beethoven")),
        person("Beethoven, Louis van"),
    )
    auto, review = dedupe_persons(session)
    assert (auto, review) == (1, 0)
    people = _by_label(session)
    # The fuller name "Ludwig van Beethoven" (19 chars) vs "Louis van Beethoven" (18 chars)
    canonical = people["Beethoven, Ludwig van"]
    dup = people["Beethoven, Louis van"]
    assert dup.canonical_entity_id == canonical.id
