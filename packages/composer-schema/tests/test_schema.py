"""Contract tests: refresh-cadence staleness logic and the document/adapter factories."""

from datetime import UTC, datetime, timedelta

import pytest
from composer_schema import (
    EntityDocument,
    RefreshCadence,
    SourceClaim,
    WorkMentionDocument,
    deserialize_document,
    is_due,
    serialize_document,
)
from composer_schema.testing import FakeSource, mention, person

NOW = datetime(2024, 6, 1, tzinfo=UTC)


def test_static_cadence_is_never_due() -> None:
    assert RefreshCadence.STATIC.interval is None
    assert is_due(RefreshCadence.STATIC, last_started_at=None, now=NOW) is False


def test_never_run_source_is_due() -> None:
    assert is_due(RefreshCadence.WEEKLY, last_started_at=None, now=NOW) is True


def test_due_once_interval_elapsed() -> None:
    fresh = NOW - timedelta(days=3)
    stale = NOW - timedelta(days=10)
    assert is_due(RefreshCadence.WEEKLY, fresh, NOW) is False
    assert is_due(RefreshCadence.WEEKLY, stale, NOW) is True


def test_naive_last_started_at_treated_as_utc() -> None:
    # SQLite hands back naive datetimes; is_due must not raise on the comparison.
    naive_stale = datetime(2024, 5, 1)  # noqa: DTZ001 — intentional naive input
    assert is_due(RefreshCadence.WEEKLY, naive_stale, NOW) is True


def test_person_factory_builds_entity_document_with_claims() -> None:
    doc = person("Beethoven", SourceClaim(predicate="born_on", value="1770-12-17"))
    assert isinstance(doc, EntityDocument)
    assert doc.name == "Beethoven"
    assert doc.claims[0].predicate == "born_on"


def test_mention_factory_builds_work_mention_document() -> None:
    doc = mention("Symphony No. 5", "Beethoven")
    assert isinstance(doc, WorkMentionDocument)
    assert doc.title == "Symphony No. 5"
    assert doc.composer == "Beethoven"


def test_fake_source_yields_then_fails_after() -> None:
    src = FakeSource(records=(person("a"), person("b"), person("c")), fail_after=2)
    out: list[str] = []
    try:
        for doc in src.fetch():
            assert isinstance(doc, EntityDocument)
            out.append(doc.name)
    except RuntimeError as exc:
        assert "exploded" in str(exc)
    assert out == ["a", "b"]


def test_serialize_deserialize_entity_round_trip() -> None:
    doc = person(
        "Beethoven, Ludwig van",
        SourceClaim(predicate="has_profession", object_kind="profession", object_label="composer"),
        SourceClaim(predicate="born_on", value="1770-12-17"),
    )
    assert deserialize_document(serialize_document(doc)) == doc


def test_serialize_deserialize_work_mention_round_trip() -> None:
    doc = mention("Symphony No. 5", "Beethoven, Ludwig van")
    assert deserialize_document(serialize_document(doc)) == doc


def test_serialize_tags_type() -> None:
    assert serialize_document(person("x"))["_type"] == "entity"
    assert serialize_document(mention("t", "c"))["_type"] == "work_mention"


def test_deserialize_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown _type"):
        deserialize_document({"_type": "bogus", "ingested_at": "2024-01-01T00:00:00+00:00"})
