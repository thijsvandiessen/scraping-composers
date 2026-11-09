"""Ingest keeps the language a source states for a record's name or title."""

from dataclasses import replace

from composer_models import EntityRecord, RawWorkMention
from composer_warehouse.testing import FakeSource, ingest_source, mention, person
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_a_stated_language_is_stored_with_the_record(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(
            records=(
                replace(person("Budapester Sinfonieorchester"), language="de"),
                replace(mention("Sinfonie Nr. 5 c-moll", "Beethoven, Ludwig van"), language="de"),
                person("Beethoven, Ludwig van"),
            )
        ),
    )
    records = {r.name: r.language for r in session.scalars(select(EntityRecord))}
    assert records == {"Budapester Sinfonieorchester": "de", "Beethoven, Ludwig van": None}
    assert session.scalars(select(RawWorkMention.language)).one() == "de"


def test_a_resighting_fills_in_the_language_without_rematching(session: Session) -> None:
    # first seen before the source stated a language; the content is unchanged
    title = mention("Sinfonie Nr. 5 c-moll", "Beethoven, Ludwig van")
    ingest_source(session, FakeSource(records=(person("Ensemble X"), title)))
    ingest_source(
        session,
        FakeSource(records=(replace(person("Ensemble X"), language="mul"), replace(title, language="de"))),
    )
    assert session.scalars(select(EntityRecord.language)).one() == "mul"
    row = session.scalars(select(RawWorkMention)).one()
    assert row.language == "de"
    # a re-match would have found the work the first sighting created
    assert row.match_status == "created"
