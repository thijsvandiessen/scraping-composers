"""Ingest pipeline tests against fake in-memory sources (no network)."""

from datetime import UTC, datetime

from composer_models import Claim, Entity, EntityRecord, IngestRun
from composer_schema import EntityDocument, SourceClaim
from composer_warehouse.testing import FakeSource, ingest_source, mention, person
from sqlalchemy import func, select
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


def entities_by_kind(session: Session) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for entity in session.scalars(select(Entity).order_by(Entity.kind, Entity.label)):
        result.setdefault(entity.kind, []).append(entity.label)
    return result


def test_ingest_creates_entities_records_and_claims(session: Session) -> None:
    run = ingest_source(session, FakeSource(records=(MOZART,)))

    assert run.status == "completed"
    assert run.records_seen == 1
    assert run.records_new == 1

    assert entities_by_kind(session) == {
        "person": ["Mozart, Wolfgang Amadeus"],
        "profession": ["composer", "pianist"],
        "period": ["Classical"],
        "place": ["Salzburg"],
        "work": ["Requiem in D minor"],
    }

    record = session.scalars(select(EntityRecord)).one()
    claims = session.scalars(select(Claim)).all()
    assert len(claims) == 7  # 6 source claims + 1 auto-injected mentioned_in
    for claim in claims:
        # every claim carries full provenance back to its source and record
        assert claim.source.name == "fake"
        assert claim.record_id == record.id
        assert claim.subject.label == "Mozart, Wolfgang Amadeus"

    born_on = session.scalars(select(Claim).where(Claim.predicate == "born_on")).one()
    assert born_on.object_id is None
    assert born_on.value == "1756-01-27"

    born_in = session.scalars(select(Claim).where(Claim.predicate == "born_in")).one()
    assert born_in.object is not None
    assert (born_in.object.kind, born_in.object.label) == ("place", "Salzburg")


def test_second_source_attaches_to_same_entity(session: Session) -> None:
    imslp_like = FakeSource(
        records=(
            person(
                "Beethoven, Ludwig van",
                SourceClaim("born_in", "place", "Bonn"),
                external_id="Category:Beethoven, Ludwig van",
            ),
        ),
        name="source-a",
    )
    wiki_like = FakeSource(
        records=(
            person(
                "Ludwig van Beethoven",  # different formatting, same dedup key
                SourceClaim("has_profession", "profession", "Composer"),
                SourceClaim("born_in", "place", "Vienna"),  # conflicting fact
                external_id="Q255",
            ),
        ),
        name="source-b",
    )

    ingest_source(session, imslp_like)
    ingest_source(session, wiki_like)

    people = session.scalars(select(Entity).where(Entity.kind == "person")).all()
    assert len(people) == 1  # deduplicated across sources
    assert len(people[0].records) == 2  # but both raw records kept

    # conflicting claims coexist, each with its own source
    born_in = session.scalars(select(Claim).where(Claim.predicate == "born_in")).all()
    assert sorted((c.source.name, c.object.label) for c in born_in if c.object) == [
        ("source-a", "Bonn"),
        ("source-b", "Vienna"),
    ]


def test_claim_objects_are_deduplicated_entities(session: Session) -> None:
    source = FakeSource(
        records=(
            person("Mozart, Wolfgang Amadeus", SourceClaim("has_profession", "profession", "composer")),
            person("Haydn, Joseph", SourceClaim("has_profession", "profession", "Composer")),
        ),
    )
    ingest_source(session, source)

    professions = session.scalars(select(Entity).where(Entity.kind == "profession")).all()
    assert len(professions) == 1  # "composer" and "Composer" normalize to one entity
    claims = session.scalars(select(Claim)).all()
    assert {claim.subject_id for claim in claims} == {
        entity.id for entity in session.scalars(select(Entity).where(Entity.kind == "person"))
    }


def test_mentioned_in_uses_record_url_when_present(session: Session) -> None:
    source = FakeSource(
        records=(
            EntityDocument(
                id="abc",
                url="https://example.com/mozart",
                source_name="fake",
                ingested_at=_INGESTED_AT,
                name="Mozart, Wolfgang Amadeus",
                raw={"id": "abc"},
                claims=(),
            ),
        )
    )
    ingest_source(session, source)

    claim = session.scalars(select(Claim).where(Claim.predicate == "mentioned_in")).one()
    assert claim.value == "https://example.com/mozart"


def test_mentioned_in_falls_back_to_source_base_url(session: Session) -> None:
    source = FakeSource(
        records=(person("Haydn, Joseph"),),  # person() leaves url=None
        base_url="https://fake.example/composers",
    )
    ingest_source(session, source)

    claim = session.scalars(select(Claim).where(Claim.predicate == "mentioned_in")).one()
    assert claim.value == "https://fake.example/composers"


def test_batch_commit_fires_at_1000_record_boundary(session: Session) -> None:
    # COMMIT_BATCH=1000: this exercises the mid-run commit path at seen==1000
    source = FakeSource(records=tuple(person(f"Composer {i}") for i in range(1001)))
    run = ingest_source(session, source)

    assert run.status == "completed"
    assert run.records_seen == 1001
    assert run.records_new == 1001
    count = session.scalar(select(func.count(Entity.id)).where(Entity.kind == "person"))
    assert count == 1001


def test_failing_source_marks_run_failed(session: Session) -> None:
    source = FakeSource(records=(MOZART, person("Haydn, Joseph")), fail_after=1)
    run = ingest_source(session, source)

    assert run.status == "failed"
    assert run.error is not None and "source exploded" in run.error
    assert run.finished_at is not None

    # the failure is recorded in the run log
    logged = session.scalars(select(IngestRun)).one()
    assert logged.status == "failed"

    # records processed before the error are preserved
    assert run.records_seen == 1
    assert run.records_new == 1
    people = session.scalars(select(Entity).where(Entity.kind == "person")).all()
    assert len(people) == 1  # Mozart was committed before the error


def test_entity_has_ingestion_timestamps(session: Session) -> None:
    ingest_source(session, FakeSource(records=(MOZART,)))
    entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()

    assert entity.first_ingested_at is not None
    assert entity.last_ingested_at is not None
    assert entity.last_edited_at is not None


def test_two_records_for_one_person_union_their_claims(session: Session) -> None:
    """A crawl running several extract kinds describes the same person once per
    kind. The documents carry distinct external ids, so both are recorded and
    both sets of claims reach the one entity — an id reused across kinds would be
    read as a re-sighting, which adds no claims at all."""
    from_concerts = EntityDocument(
        id="person:Beethoven",
        url="https://www.laphil.com/works/violin-concerto-beethoven",
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name="Beethoven",
        claims=(SourceClaim("has_profession", "profession", "composer"),),
    )
    from_claims = EntityDocument(
        id="claims:person:Beethoven",
        url="https://www.laphil.com/works/violin-concerto-beethoven",
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name="Beethoven",
        claims=(SourceClaim("composed", "work", "Beethoven: Violin Concerto"),),
    )
    ingest_source(session, FakeSource(records=(from_concerts, from_claims)))

    entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()
    assert session.scalar(select(func.count()).select_from(EntityRecord)) == 2
    predicates = {c.predicate for c in session.scalars(select(Claim).where(Claim.subject_id == entity.id))}
    assert {"has_profession", "composed"} <= predicates


def test_ensemble_named_person_records_land_as_ensembles(session: Session) -> None:
    """Sources credit an orchestra the same way they credit a violinist, so a
    participant reaches ingest as a ``person`` whatever it names (#174). The
    label settles it before the kind is baked into the entity's uuid."""
    ingest_source(
        session,
        FakeSource(
            records=(
                person("Malmö Symphony Orchestra", external_id="p:mso"),
                person("Tölzer Knabenchor", external_id="p:tk"),
                person(
                    "Rattle, Sir Simon",
                    SourceClaim("performed_with", "person", "Berliner Philharmoniker"),
                    external_id="p:rattle",
                ),
            )
        ),
    )

    assert entities_by_kind(session) == {
        "ensemble": ["Berliner Philharmoniker", "Malmö Symphony Orchestra", "Tölzer Knabenchor"],
        "person": ["Rattle, Sir Simon"],
    }


def test_ensemble_named_mention_composer_lands_as_an_ensemble(session: Session) -> None:
    """The composer credit on a work mention mints an entity too, and an album
    that credits the orchestra in that slot must not mint a person for it."""
    ingest_source(
        session,
        FakeSource(records=(mention("Symphony No. 5", "Boston Symphony Orchestra", external_id="m:1"),)),
    )

    entity = session.scalars(select(Entity).where(Entity.label == "Boston Symphony Orchestra")).one()
    assert entity.kind == "ensemble"
