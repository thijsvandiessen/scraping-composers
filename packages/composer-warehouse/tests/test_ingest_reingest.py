"""Ingest tests for re-sighted entity records: idempotency and content changes.

Split out of test_ingest.py to keep each module to one concern.
"""

import json
from datetime import UTC, datetime

from composer_models import Claim, Entity, EntityRecord
from composer_schema import EntityDocument, SourceClaim
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy import select
from sqlalchemy.orm import Session

_INGESTED_AT = datetime(2024, 1, 1, tzinfo=UTC)


MOZART = person(
    "Mozart, Wolfgang Amadeus",
    SourceClaim("has_profession", "profession", "composer"),
    SourceClaim("has_profession", "profession", "pianist"),
    SourceClaim("associated_period", "period", "Classical"),
    SourceClaim("born_in", "place", "Salzburg"),
    SourceClaim("born_on", value="1756-01-27"),
    SourceClaim("composed", "work", "Requiem in D minor"),
)


def test_reingest_is_idempotent(session: Session) -> None:
    source = FakeSource(records=(MOZART, person("Haydn, Joseph")))
    first = ingest_source(session, source)
    second = ingest_source(session, source)

    assert (first.records_seen, first.records_new) == (2, 2)
    assert (second.records_seen, second.records_new) == (2, 0)
    assert session.scalar(select(Entity.id).where(Entity.kind == "person")) is not None
    assert len(session.scalars(select(EntityRecord)).all()) == 2
    assert len(session.scalars(select(Claim)).all()) == 8  # 6 Mozart + 2 mentioned_in, nothing duplicated

    # re-ingest refreshes provenance: records now point at the second run
    for record in session.scalars(select(EntityRecord)):
        assert record.first_run_id == first.id
        assert record.last_run_id == second.id
        assert record.last_seen_at >= record.first_seen_at


def test_reingest_with_changed_content_updates_record(session: Session) -> None:
    """A re-sighted external id whose content genuinely changed (not just a
    byte-identical replay) must update the stored row and persist any new
    claims, not just bump the timestamp (issue #137)."""
    first_doc = EntityDocument(
        id="wikidata:Q1234",
        url="https://example.com/old",
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name="Old Name",
        raw={"version": 1},
        claims=(SourceClaim("born_in", "place", "Salzburg"),),
    )
    second_doc = EntityDocument(
        id="wikidata:Q1234",
        url="https://example.com/new",
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name="New Name",
        raw={"version": 2},
        claims=(
            SourceClaim("born_in", "place", "Salzburg"),
            SourceClaim("died_in", "place", "Vienna"),
        ),
    )
    first = ingest_source(session, FakeSource(records=(first_doc,)))
    second = ingest_source(session, FakeSource(records=(second_doc,)))

    assert (first.records_new, second.records_new) == (1, 0)

    record = session.scalars(select(EntityRecord)).one()
    assert record.name == "New Name"
    assert record.url == "https://example.com/new"
    assert json.loads(record.raw) == {"version": 2}
    assert record.first_run_id == first.id
    assert record.last_run_id == second.id

    claims = session.scalars(select(Claim).where(Claim.record_id == record.id)).all()
    facts = {(c.predicate, c.object.label if c.object else c.value) for c in claims}
    assert ("died_in", "Vienna") in facts  # the new claim was persisted
    assert ("born_in", "Salzburg") in facts  # the old claim still coexists


def test_reingest_with_unchanged_content_adds_no_duplicate_claims(session: Session) -> None:
    doc = EntityDocument(
        id="wikidata:Q99",
        url="https://example.com/same",
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name="Same Name",
        raw={"version": 1},
        claims=(SourceClaim("born_in", "place", "Salzburg"),),
    )
    source = FakeSource(records=(doc,))
    ingest_source(session, source)
    ingest_source(session, source)

    record = session.scalars(select(EntityRecord)).one()
    assert record.name == "Same Name"
    assert json.loads(record.raw) == {"version": 1}

    claims = session.scalars(select(Claim).where(Claim.record_id == record.id)).all()
    assert len(claims) == 2  # born_in + mentioned_in, not duplicated by the second run


def _named_doc(name: str, version: int) -> EntityDocument:
    """The same record seen twice under a corrected name: only ``name`` and the
    raw payload differ, so the content hash changes but the identity doesn't."""
    return EntityDocument(
        id="wikidata:Q1234",
        url="https://example.com/old",
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name=name,
        raw={"version": version},
        claims=(),
    )


def test_reingest_with_changed_name_updates_entity_label_only(session: Session) -> None:
    """A re-sighted record's corrected name updates the shared Entity's label
    (cosmetic rename), but must not touch dedup_key/id — those are derived
    from the label at creation time (composer_models.normalize.entity_uuid),
    so changing them in place would desync id from dedup_key and break future
    dedup lookups (issue #142)."""
    ingest_source(session, FakeSource(records=(_named_doc("Old Name", 1),)))
    entity = session.scalars(select(Entity)).one()
    original_id, original_dedup_key = entity.id, entity.dedup_key

    ingest_source(session, FakeSource(records=(_named_doc("New Name", 2),)))
    session.expire(entity)

    assert entity.label == "New Name"
    assert entity.id == original_id
    assert entity.dedup_key == original_dedup_key


def test_reingest_with_changed_name_advances_last_edited_at(session: Session) -> None:
    """The rename must mark the entity *edited*, not merely seen. Asserted
    strictly (``>``, not ``>=``): the ``mentioned_in`` claim already exists on
    the second run, so without the label update nothing would add the entity to
    ``edited_entity_ids`` and last_edited_at would simply stay put."""
    ingest_source(session, FakeSource(records=(_named_doc("Old Name", 1),)))
    entity = session.scalars(select(Entity)).one()
    edited_after_first = entity.last_edited_at

    ingest_source(session, FakeSource(records=(_named_doc("New Name", 2),)))
    session.expire(entity)

    assert entity.last_edited_at > edited_after_first


def test_reingest_updates_last_ingested_at(session: Session) -> None:
    source = FakeSource(records=(MOZART,))
    ingest_source(session, source)

    entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()
    after_first = entity.last_ingested_at

    ingest_source(session, source)
    session.expire(entity)

    assert entity.last_ingested_at >= after_first


def test_last_edited_at_unchanged_on_reingest_with_same_claims(session: Session) -> None:
    source = FakeSource(records=(MOZART,))
    ingest_source(session, source)

    entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()
    edited_after_first = entity.last_edited_at

    ingest_source(session, source)
    session.expire(entity)

    # no new claims were added, so last_edited_at should not advance
    assert entity.last_edited_at == edited_after_first
