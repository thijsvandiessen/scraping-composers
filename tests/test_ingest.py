"""Ingest pipeline tests against fake in-memory sources (no network)."""

from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from composer_ingest.ingestion import run_ingest
from composer_ingest.models import Claim, Entity, EntityRecord, IngestRun
from composer_ingest.sources import SourceClaim, SourceRecord, SourceWorkMention


@dataclass
class FakeSource:
    """In-memory stand-in for a source module (satisfies ``SourceLike``)."""

    records: tuple[SourceRecord | SourceWorkMention, ...]
    NAME: str = "fake"
    BASE_URL: str = "https://fake.example"
    fail_after: int | None = None

    def fetch_records(self, max_pages: int | None = None) -> Iterator[SourceRecord | SourceWorkMention]:
        for i, record in enumerate(self.records):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("source exploded")
            yield record


def person(name: str, *claims: SourceClaim, external_id: str | None = None) -> SourceRecord:
    return SourceRecord(
        external_id=external_id or f"Category:{name}",
        name=name,
        url=None,
        raw={"id": name},
        claims=claims,
    )


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
    run = run_ingest(session, FakeSource(records=(MOZART,)))

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


def test_reingest_is_idempotent(session: Session) -> None:
    source = FakeSource(records=(MOZART, person("Haydn, Joseph")))
    first = run_ingest(session, source)
    second = run_ingest(session, source)

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


def test_second_source_attaches_to_same_entity(session: Session) -> None:
    imslp_like = FakeSource(
        records=(
            person(
                "Beethoven, Ludwig van",
                SourceClaim("born_in", "place", "Bonn"),
                external_id="Category:Beethoven, Ludwig van",
            ),
        ),
        NAME="source-a",
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
        NAME="source-b",
    )

    run_ingest(session, imslp_like)
    run_ingest(session, wiki_like)

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
    run_ingest(session, source)

    professions = session.scalars(select(Entity).where(Entity.kind == "profession")).all()
    assert len(professions) == 1  # "composer" and "Composer" normalize to one entity
    claims = session.scalars(select(Claim)).all()
    assert {claim.subject_id for claim in claims} == {
        entity.id for entity in session.scalars(select(Entity).where(Entity.kind == "person"))
    }


def test_mentioned_in_uses_record_url_when_present(session: Session) -> None:
    source = FakeSource(
        records=(
            SourceRecord(
                external_id="abc",
                name="Mozart, Wolfgang Amadeus",
                url="https://example.com/mozart",
                raw={"id": "abc"},
                claims=(),
            ),
        )
    )
    run_ingest(session, source)

    claim = session.scalars(select(Claim).where(Claim.predicate == "mentioned_in")).one()
    assert claim.value == "https://example.com/mozart"


def test_mentioned_in_falls_back_to_source_base_url(session: Session) -> None:
    source = FakeSource(
        records=(person("Haydn, Joseph"),),  # person() leaves url=None
        BASE_URL="https://fake.example/composers",
    )
    run_ingest(session, source)

    claim = session.scalars(select(Claim).where(Claim.predicate == "mentioned_in")).one()
    assert claim.value == "https://fake.example/composers"


def test_batch_commit_fires_at_1000_record_boundary(session: Session) -> None:
    # COMMIT_BATCH=1000: this exercises the mid-run commit path at seen==1000
    source = FakeSource(records=tuple(person(f"Composer {i}") for i in range(1001)))
    run = run_ingest(session, source)

    assert run.status == "completed"
    assert run.records_seen == 1001
    assert run.records_new == 1001
    count = session.scalar(select(func.count(Entity.id)).where(Entity.kind == "person"))
    assert count == 1001


def test_failing_source_marks_run_failed(session: Session) -> None:
    source = FakeSource(records=(MOZART, person("Haydn, Joseph")), fail_after=1)
    run = run_ingest(session, source)

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
    run_ingest(session, FakeSource(records=(MOZART,)))
    entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()

    assert entity.first_ingested_at is not None
    assert entity.last_ingested_at is not None
    assert entity.last_edited_at is not None


def test_reingest_updates_last_ingested_at(session: Session) -> None:
    source = FakeSource(records=(MOZART,))
    run_ingest(session, source)

    entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()
    after_first = entity.last_ingested_at

    run_ingest(session, source)
    session.expire(entity)

    assert entity.last_ingested_at >= after_first


def test_last_edited_at_unchanged_on_reingest_with_same_claims(session: Session) -> None:
    source = FakeSource(records=(MOZART,))
    run_ingest(session, source)

    entity = session.scalars(select(Entity).where(Entity.kind == "person")).one()
    edited_after_first = entity.last_edited_at

    run_ingest(session, source)
    session.expire(entity)

    # no new claims were added, so last_edited_at should not advance
    assert entity.last_edited_at == edited_after_first
