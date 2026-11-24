"""Placeholder names ("Anonymous", "Various", "Audience") never become persons."""

from dataclasses import replace

import pytest
from composer_models import Claim, Entity, EntityRecord, RawWorkMention, Work
from composer_schema import SourceClaim
from composer_warehouse.ingestion.entities import is_placeholder_person
from composer_warehouse.testing import FakeSource, ingest_source, mention, person
from sqlalchemy import select
from sqlalchemy.orm import Session


def _persons(session: Session) -> set[str]:
    return {e.label for e in session.scalars(select(Entity).where(Entity.kind == "person"))}


@pytest.mark.parametrize(
    "label",
    [
        "Anonymous",
        "Anonymus",
        "Anon.",
        "Anónimo",
        "Traditional",
        "Trad.",
        "Various Artists",
        "N.N.",
        "onbekend,",
        "Audience",
    ],
)
def test_placeholders_are_recognised_in_any_spelling(label: str) -> None:
    assert is_placeholder_person(label)


@pytest.mark.parametrize(
    "label", ["Anonymous 4", "Anonymous IV", "Anonymous of Beauvais", "Midori", "Unknown Soloist"]
)
def test_a_name_merely_containing_one_is_a_person(label: str) -> None:
    """Exact on the whole key: the ensemble Anonymous 4 and the theorist Anonymous IV are real."""
    assert not is_placeholder_person(label)


def test_a_placeholder_composer_leaves_the_work_without_one(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(
            records=(mention("Greensleeves", "Traditional", "m1"), mention("Ave Maria", "Anonymous", "m2"))
        ),
    )

    assert _persons(session) == set()
    assert all(w.composer_entity_id is None for w in session.scalars(select(Work)))
    rows = session.scalars(select(RawWorkMention).order_by(RawWorkMention.external_id)).all()
    # the attribution itself is kept verbatim
    assert [(r.raw_composer, r.composer_entity_id) for r in rows] == [
        ("Traditional", None),
        ("Anonymous", None),
    ]


def test_a_resighted_mention_whose_composer_became_a_placeholder(session: Session) -> None:
    ingest_source(session, FakeSource(records=(mention("Ave Maria", "Arcadelt, Jacques", "m1"),)))
    ingest_source(session, FakeSource(records=(mention("Ave Maria", "Anonymous", "m1"),)))

    row = session.scalars(select(RawWorkMention)).one()
    assert (row.raw_composer, row.composer_entity_id) == ("Anonymous", None)
    assert "Anonymous" not in _persons(session)


def test_a_claim_pointing_at_a_placeholder_is_dropped(session: Session) -> None:
    work = replace(
        person(
            "Greensleeves",
            SourceClaim("composed_by", object_kind="person", object_label="Traditional"),
            SourceClaim("has_genre", object_kind="genre", object_label="folk song"),
            external_id="w1",
        ),
        kind="work",
    )
    ingest_source(session, FakeSource(records=(work,)))

    assert _persons(session) == set()
    assert {c.predicate for c in session.scalars(select(Claim))} == {"mentioned_in", "has_genre"}


def test_a_source_record_for_a_placeholder_is_kept_without_an_entity(session: Session) -> None:
    """nyphil lists "Anonymous" as a composer record: bronze keeps it, silver
    mints no person for it, and seeing it again changes nothing."""
    records = (
        person("Anonymous", SourceClaim("has_profession", object_kind="profession", object_label="composer")),
    )
    ingest_source(session, FakeSource(records=records))
    ingest_source(session, FakeSource(records=records))

    record = session.scalars(select(EntityRecord)).one()
    assert (record.name, record.entity_id) == ("Anonymous", None)
    assert _persons(session) == set()
    assert session.scalars(select(Claim)).all() == []
