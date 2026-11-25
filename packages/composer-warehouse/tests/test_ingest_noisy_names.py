"""Annotations sources wrap around a person's name are not part of the person (#457)."""

import logging
from dataclasses import replace

import pytest
from composer_models import Entity, EntityRecord, RawWorkMention
from composer_schema import SourceClaim
from composer_warehouse.ingestion.entities import clean_person_label, is_placeholder_person
from composer_warehouse.testing import FakeSource, ingest_source, mention, person
from sqlalchemy import select
from sqlalchemy.orm import Session


def _persons(session: Session) -> set[str]:
    return {e.label for e in session.scalars(select(Entity).where(Entity.kind == "person"))}


@pytest.mark.parametrize(
    "label,expected",
    [
        ("[Mozart](https://theclassicreview.com/tag/mozart/)", "Mozart"),
        ("[Dvořák](https://theclassicreview.com/tag/dvorak/)", "Dvořák"),
        ("[Senfl, Ludwig]", "Senfl, Ludwig"),
        ("Bartok [Bartók], Béla", "Bartók, Béla"),
        ("Bucharoff [Buchalter], Simon", "Bucharoff [Buchalter], Simon"),  # not a respelling
        ("Frédéric Chopin (arr. Liszt)", "Frédéric Chopin"),
        ("Bach (Arr: Gounod)", "Bach"),
        ("Franz Schubert (arranged by Franz Liszt)", "Franz Schubert"),
        ("Bob Chilcott (arrangement)", "Bob Chilcott"),
        ("Crüger, Johann (nach Loys Bourgeois)", "Crüger, Johann"),
        ("Britten - Tircuit, Heuwell (Arranger); Davies, Hywel (Arranger)", "Britten"),
        ("Copland - Karr, Gary (Arranger); Kohn, Andrew (Editor)", "Copland"),
        ("Ożelska - Kurzawa, Marta", "Ożelska - Kurzawa, Marta"),  # a hyphenated surname, no credit
        # untouched: a parenthesis that is not an arranger note, a bracket inside a name
        ("Tobia (Arrigo Boito) Gorrio", "Tobia (Arrigo Boito) Gorrio"),
        ("Plançon, Pol (Paul-Henri)", "Plançon, Pol (Paul-Henri)"),
        ("Esther Baezner [-Vogel]", "Esther Baezner [-Vogel]"),
        # a label that would clean to nothing is kept as it is
        ("(arr. Liszt)", "(arr. Liszt)"),
    ],
)
def test_clean_person_label(label: str, expected: str) -> None:
    assert clean_person_label(label) == expected


def test_a_placeholder_behind_markup_is_still_one() -> None:
    assert is_placeholder_person("[Anonymous](https://theclassicreview.com/tag/anonymous/)")
    assert is_placeholder_person("Traditional (arr. Britten)")


def test_a_wikidata_unknown_value_is_no_person(session: Session) -> None:
    """An older wikidata_works snapshot names a composer by its blank-node URL."""
    genid = "http://www.wikidata.org/.well-known/genid/129ee64504a4654b94546bef44b562f5"
    assert is_placeholder_person(genid)
    work = replace(
        person(
            "Sonata", SourceClaim("composed_by", object_kind="person", object_label=genid), external_id="w1"
        ),
        kind="work",
    )
    ingest_source(session, FakeSource(records=(work,)))

    assert _persons(session) == set()


def test_a_markdown_record_becomes_the_plain_name(session: Session) -> None:
    """theclassicreview's tag link folds into the "Mozart" another source names."""
    ingest_source(
        session,
        FakeSource(
            records=(person("[Mozart](https://theclassicreview.com/tag/mozart/)"),), name="theclassicreview"
        ),
    )
    ingest_source(session, FakeSource(records=(person("Mozart"),), name="other"))

    assert _persons(session) == {"Mozart"}
    records = session.scalars(select(EntityRecord).order_by(EntityRecord.id)).all()
    assert records[0].name == "[Mozart](https://theclassicreview.com/tag/mozart/)"  # bronze stays verbatim
    assert records[0].entity_id == records[1].entity_id


def test_an_arranger_note_is_dropped_from_a_mention_and_a_claim(session: Session) -> None:
    work = replace(
        person(
            "Hungarian Rhapsody",
            SourceClaim("composed_by", object_kind="person", object_label="Franz Liszt (arr. Doppler)"),
            external_id="w1",
        ),
        kind="work",
    )
    ingest_source(
        session, FakeSource(records=(work, mention("Nocturne", "Frédéric Chopin (arr. Liszt)", "m1")))
    )

    assert _persons(session) == {"Franz Liszt", "Frédéric Chopin"}
    row = session.scalars(select(RawWorkMention)).one()
    assert row.raw_composer == "Frédéric Chopin (arr. Liszt)"  # the attribution is kept verbatim
    assert row.composer_entity_id is not None


def test_a_bracketed_spelling_in_an_older_snapshot_folds_into_the_new_name(session: Session) -> None:
    """Bronze keeps nyphil runs written before the scraper applied the spelling,
    and a rebuild replays them: both runs must land on one person."""
    ingest_source(session, FakeSource(records=(person("Bartok [Bartók], Béla", external_id="composer:old"),)))
    ingest_source(session, FakeSource(records=(person("Bartók, Béla", external_id="composer:new"),)))

    assert _persons(session) == {"Bartók, Béla"}


def test_a_label_still_carrying_markup_is_logged_not_dropped(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="composer_warehouse.ingestion.entities"):
        ingest_source(session, FakeSource(records=(person("test [unbekannt]"), person("Mozart"))))

    assert _persons(session) == {"test [unbekannt]", "Mozart"}
    assert [r.getMessage() for r in caplog.records] == [
        "person label still carries markup or a URL: 'test [unbekannt]'"
    ]
