"""Tests for the gold.db composer query ``imslp_works`` is scoped to.

The fixture database is written with the shared ``composer_models`` schema —
the same models the promote step builds gold.db with — so these tests break
if the query and the real schema ever drift apart.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from composer_models import Claim, Entity, EntityRecord, IngestRun, Source
from composer_models.db import get_engine, init_db
from composer_scrapers.imslp_works.gold import composers

BEETHOVEN_ID = uuid.uuid4()
BARTOK_ID = uuid.uuid4()
KARAJAN_ID = uuid.uuid4()
IMSLP_URL = "https://imslp.org/wiki/Category:Beethoven,_Ludwig_van"


def _entity(entity_id: uuid.UUID, kind: str, label: str) -> Entity:
    return Entity(id=entity_id, kind=kind, dedup_key=label.lower(), label=label)


def _record(source: Source, run: IngestRun, entity_id: uuid.UUID, external_id: str, url: str) -> EntityRecord:
    return EntityRecord(
        source_id=source.id,
        entity_id=entity_id,
        external_id=external_id,
        name="Beethoven, Ludwig van",
        url=url,
        raw="{}",
        first_run_id=run.id,
        last_run_id=run.id,
    )


@pytest.fixture
def gold_db(tmp_path: Path) -> str:
    """Three persons (one conductor-only), their profession claims, and an
    IMSLP plus a Wikidata record for Beethoven."""
    db_path = tmp_path / "gold.db"
    factory = init_db(get_engine(f"sqlite:///{db_path}"))
    with factory() as session:
        imslp = Source(name="imslp")
        wikidata = Source(name="wikidata")
        session.add_all([imslp, wikidata])
        session.flush()
        run = IngestRun(source_id=imslp.id, status="completed")
        session.add(run)
        session.flush()

        composer = _entity(uuid.uuid4(), "profession", "composer")
        composer_conductor = _entity(uuid.uuid4(), "profession", "composer, conductor")
        conductor = _entity(uuid.uuid4(), "profession", "conductor")
        session.add_all(
            [
                _entity(BEETHOVEN_ID, "person", "Beethoven, Ludwig van"),
                _entity(BARTOK_ID, "person", "Bartok, Bela"),
                _entity(KARAJAN_ID, "person", "Karajan, Herbert von"),  # conductor only
                composer,
                composer_conductor,
                conductor,
            ]
        )
        claims = {BEETHOVEN_ID: composer, BARTOK_ID: composer_conductor, KARAJAN_ID: conductor}
        session.add_all(
            Claim(
                subject_id=subject_id,
                predicate="has_profession",
                object_id=profession.id,
                source_id=wikidata.id,
            )
            for subject_id, profession in claims.items()
        )
        # The Wikidata record's URL sorts before the IMSLP one ("http:" < "https:"),
        # so only the source-name filter keeps it out of known_imslp_url.
        session.add_all(
            [
                _record(imslp, run, BEETHOVEN_ID, "Category:Beethoven,_Ludwig_van", IMSLP_URL),
                _record(wikidata, run, BEETHOVEN_ID, "Q255", "http://www.wikidata.org/wiki/Q255"),
            ]
        )
        session.commit()
    return str(db_path)


def test_composers_includes_only_composer_professed_persons_sorted_by_label(gold_db: str) -> None:
    assert [c.label for c in composers(gold_db)] == ["Bartok, Bela", "Beethoven, Ludwig van"]


def test_composers_matches_compound_profession_labels(gold_db: str) -> None:
    """ "composer, conductor" still counts as a composer."""
    labels = {c.label for c in composers(gold_db)}
    assert "Bartok, Bela" in labels


def test_composers_carries_the_known_imslp_url(gold_db: str) -> None:
    result = {c.label: c for c in composers(gold_db)}
    assert result["Beethoven, Ludwig van"].known_imslp_url == IMSLP_URL


def test_composers_leaves_unlinked_composers_with_no_url(gold_db: str) -> None:
    result = {c.label: c for c in composers(gold_db)}
    assert result["Bartok, Bela"].known_imslp_url is None


def test_composers_reports_the_entity_uuid_as_a_string(gold_db: str) -> None:
    result = {c.label: c for c in composers(gold_db)}
    assert result["Beethoven, Ludwig van"].entity_id == str(BEETHOVEN_ID)
