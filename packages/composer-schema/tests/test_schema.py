"""Contract tests: refresh-cadence staleness logic and the document/adapter factories."""

from dataclasses import replace
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


def test_a_document_states_no_language_unless_given_one() -> None:
    assert person("Beethoven").language is None
    assert mention("Symphony No. 5", "Beethoven").language is None


@pytest.mark.parametrize("tag", ["en", "de", "mul", "zh-hans", "de-ch"])
def test_language_round_trips(tag: str) -> None:
    for doc in (replace(person("x"), language=tag), replace(mention("t", "c"), language=tag)):
        assert deserialize_document(serialize_document(doc)).language == tag


def test_a_document_written_before_languages_reads_as_unknown() -> None:
    # bronze written before the field existed has no "language" key
    payload = serialize_document(mention("Sinfonie Nr. 5", "Beethoven"))
    del payload["language"]
    assert deserialize_document(payload).language is None


@pytest.mark.parametrize("tag", ["", "EN", "english", "de_DE", "e"])
def test_a_language_that_is_not_a_tag_is_refused(tag: str) -> None:
    with pytest.raises(ValueError, match="not a language tag"):
        replace(person("x"), language=tag)


def test_language_is_keyword_only_so_positional_fields_keep_their_place() -> None:
    doc = WorkMentionDocument("m1", None, "fake", NOW, "Symphony No. 5", "Beethoven")
    assert (doc.title, doc.composer, doc.language) == ("Symphony No. 5", "Beethoven", None)
