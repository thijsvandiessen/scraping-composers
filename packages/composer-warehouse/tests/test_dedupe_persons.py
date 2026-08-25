"""Tests for the post-hoc person dedupe pass."""

from composer_models import Entity, PersonMatch
from composer_schema import SourceClaim
from composer_warehouse.persons import dedupe_persons, reset_person_links
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _ingest(session: Session, *people: object) -> None:
    ingest_source(session, FakeSource(records=people))  # pyright: ignore[reportArgumentType]


def _by_label(session: Session) -> dict[str, Entity]:
    return {e.label: e for e in session.scalars(select(Entity).where(Entity.kind == "person"))}


def _linked(session: Session) -> int:
    return session.scalar(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None))) or 0


def _dates(born: str, died: str) -> tuple[SourceClaim, SourceClaim]:
    return SourceClaim("born_on", value=born), SourceClaim("died_on", value=died)


def _crowd(n: int = 40) -> list[object]:
    """Filler records sharing one very common surname.

    Without them every surname in a two-record test corpus is equally frequent,
    the term-frequency adjustment is identically zero, and the tests cannot show
    the thing that makes a shared rare surname count for more than a shared
    common one. With them, "Bach" is rare and "Smith" is not.
    """
    return [person(f"Smith, Filler{i}") for i in range(n)]


def test_corroborated_dates_auto_link_a_pair(session: Session) -> None:
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685-03-31", "1750-07-28")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    auto, review = dedupe_persons(session)

    assert (auto, review) == (1, 0)
    people = _by_label(session)
    dup, canonical = people["Bach, J.S."], people["Bach, Johann Sebastian"]
    assert dup.canonical_entity_id == canonical.id  # fuller name is canonical
    assert canonical.canonical_entity_id is None

    match = session.scalars(select(PersonMatch)).one()
    assert match.status == "auto_linked"
    assert match.method == "given:initials+born:exact+died:exact"
    assert match.score >= 0.99


def test_initials_alone_no_longer_auto_link(session: Session) -> None:
    """The #173 defect, at the level of the pass.

    "J.S." against "Johann Sebastian" is genuinely compatible, but compatible
    initials on a common surname are weak evidence — the old scorer scored this
    exactly at its auto threshold and linked it, and linked "Jordan, Jules" to
    "Jordan, Julius" by the identical rule. Uncorroborated, it now goes to the
    review queue instead.
    """
    _ingest(session, person("Bach, J.S."), person("Bach, Johann Sebastian"), *_crowd())
    auto, review = dedupe_persons(session)

    assert (auto, review) == (0, 1)
    assert _linked(session) == 0
    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("needs_review", "given:initials")


def test_conflicting_given_names_are_never_proposed(session: Session) -> None:
    # The pair the issue leads with. Both reduce to the initial "j", which is
    # why the old scorer auto-linked it. Not even a rare surname rescues it.
    _ingest(session, person("Jordan, Jules"), person("Jordan, Julius"), *_crowd())
    assert dedupe_persons(session) == (0, 0)
    assert session.scalar(select(func.count(PersonMatch.id))) == 0


def test_a_corroborated_surname_only_pair_is_queued_for_review(session: Session) -> None:
    _ingest(
        session,
        person("Beethoven", SourceClaim("born_on", value="1770")),
        person("Beethoven, Ludwig van", SourceClaim("born_on", value="1770")),
        *_crowd(),
    )
    auto, review = dedupe_persons(session)

    assert (auto, review) == (0, 1)
    assert _linked(session) == 0
    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("needs_review", "given:absent+born:exact")
    assert match.entity.label == "Beethoven"  # the sparser name is the duplicate


def test_a_bare_surname_on_its_own_is_not_proposed_at_all(session: Session) -> None:
    """A bare surname against a full name, with nothing else to go on.

    The issue calls this band "irreducibly ambiguous by construction" — most
    bare surnames have several candidate full names, and the old scorer put
    46,046 such pairs in the review queue at a flat 0.70 where no reviewer
    could do better than guess. With no corroboration the model now scores them
    below the review threshold and they are simply not raised.
    """
    _ingest(session, person("Beethoven"), person("Beethoven, Ludwig van"), *_crowd())
    assert dedupe_persons(session) == (0, 0)


def test_birth_year_conflict_keeps_namesakes_separate(session: Session) -> None:
    # Same name, a generation apart: the father, not the son.
    _ingest(
        session,
        person("Strauss, Johann", *_dates("1804", "1849")),
        person("Strauss, Johann", SourceClaim("born_on", value="1825")),
    )
    auto, review = dedupe_persons(session)
    assert (auto, review) == (0, 0)
    assert _linked(session) == 0


def test_reingest_pass_is_idempotent(session: Session) -> None:
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    first = dedupe_persons(session)
    second = dedupe_persons(session)

    assert first == (1, 0)
    assert second == (0, 0)  # the decided pair is skipped on re-run
    assert session.scalar(select(func.count(PersonMatch.id))) == 1


def test_different_surnames_are_not_compared(session: Session) -> None:
    _ingest(session, person("Bach, Johann Sebastian"), person("Handel, George Frideric"))
    assert dedupe_persons(session) == (0, 0)


def test_aliases_are_used_for_matching(session: Session) -> None:
    _ingest(
        session,
        person(
            "Beethoven, Ludwig van",
            SourceClaim("also_known_as", value="Beethoven, Louis van"),
            *_dates("1770", "1827"),
        ),
        person("Beethoven, Louis van", *_dates("1770", "1827")),
    )
    auto, review = dedupe_persons(session)
    assert (auto, review) == (1, 0)
    people = _by_label(session)
    assert people["Beethoven, Louis van"].canonical_entity_id == people["Beethoven, Ludwig van"].id


def test_reset_discards_machine_links_so_the_pass_can_re_decide(session: Session) -> None:
    """#173's links are already in the database, and a re-run alone would keep
    them — the pass skips any pair that already has a match row."""
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    assert dedupe_persons(session) == (1, 0)

    deleted, unlinked = reset_person_links(session)
    assert (deleted, unlinked) == (1, 1)
    assert session.scalar(select(func.count(PersonMatch.id))) == 0
    assert _linked(session) == 0

    assert dedupe_persons(session) == (1, 0)  # free to decide the pair again


def test_reset_keeps_reviewed_decisions_and_the_links_they_justify(session: Session) -> None:
    _ingest(
        session,
        person("Beethoven", SourceClaim("born_on", value="1770")),
        person("Beethoven, Ludwig van", SourceClaim("born_on", value="1770")),
        *_crowd(),
    )
    dedupe_persons(session)
    match = session.scalars(select(PersonMatch)).one()
    match.status = "accepted"
    match.entity.canonical_entity_id = match.canonical_entity_id
    session.commit()

    deleted, unlinked = reset_person_links(session)
    assert (deleted, unlinked) == (0, 0)
    assert session.scalars(select(PersonMatch)).one().status == "accepted"
    assert _linked(session) == 1


def test_reset_drops_a_link_a_reviewer_rejected(session: Session) -> None:
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    dedupe_persons(session)
    match = session.scalars(select(PersonMatch)).one()
    match.status = "rejected"
    session.commit()

    deleted, unlinked = reset_person_links(session)
    assert (deleted, unlinked) == (0, 1)  # the row survives, the link does not
    assert session.scalars(select(PersonMatch)).one().status == "rejected"
    assert _linked(session) == 0
