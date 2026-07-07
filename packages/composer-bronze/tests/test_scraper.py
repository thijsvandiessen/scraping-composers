"""Bronze fetch-workflow tests: document serialization round-trips, the
Scraper.fetch_to_bucket manifest lifecycle, and reading typed docs back."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from composer_bronze import raw_fetch
from composer_bronze.bucket import LocalBucket
from composer_bronze.scraper import (
    Scraper,
    _deserialize,
    _serialize,
    iter_from_bucket,
    new_snapshot_id,
)
from composer_schema import (
    EntityDocument,
    SourceClaim,
    WorkMentionDocument,
)
from composer_schema.testing import FakeSource, mention, person


def test_serialize_deserialize_entity_round_trip() -> None:
    doc = person(
        "Beethoven, Ludwig van",
        SourceClaim(predicate="has_profession", object_kind="profession", object_label="composer"),
        SourceClaim(predicate="born_on", value="1770-12-17"),
    )
    assert _deserialize(_serialize(doc)) == doc


def test_serialize_deserialize_work_mention_round_trip() -> None:
    doc = mention("Symphony No. 5", "Beethoven, Ludwig van")
    assert _deserialize(_serialize(doc)) == doc


def test_serialize_tags_type() -> None:
    assert _serialize(person("x"))["_type"] == "entity"
    assert _serialize(mention("t", "c"))["_type"] == "work_mention"


def test_deserialize_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown _type"):
        _deserialize({"_type": "bogus", "ingested_at": "2024-01-01T00:00:00+00:00"})


def test_new_snapshot_id_shape_and_uniqueness() -> None:
    sid = new_snapshot_id()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-[0-9a-f]{8}", sid)
    assert new_snapshot_id() != new_snapshot_id()


def test_fetch_yields_adapter_documents() -> None:
    docs = (person("a"), mention("t", "c"))
    assert tuple(Scraper(FakeSource(records=docs)).fetch()) == docs


def test_fetch_to_bucket_writes_records_and_completed_manifest(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    source = FakeSource(records=(person("a"), person("b"), mention("t", "c")), name="fake")

    run_id = Scraper(source).fetch_to_bucket(bucket)

    manifest = bucket.read_manifest("fake", run_id)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.record_count == 3
    assert len(list(bucket.read_records("fake", run_id))) == 3


def test_fetch_to_bucket_honors_explicit_run_id(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    run_id = Scraper(FakeSource(records=(person("a"),), name="fake")).fetch_to_bucket(
        bucket, run_id="fixed-run"
    )
    assert run_id == "fixed-run"
    assert bucket.list_runs("fake") == ["fixed-run"]


def test_fetch_to_bucket_forwards_max_pages(tmp_path: Path) -> None:
    seen: list[int | None] = []

    class RecordingSource(FakeSource):
        def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
            seen.append(max_pages)
            yield from super().fetch(max_pages)

    Scraper(RecordingSource(records=(person("a"),), name="fake")).fetch_to_bucket(
        LocalBucket(tmp_path), max_pages=5
    )
    assert seen == [5]


def test_fetch_to_bucket_failure_writes_failed_manifest_and_reraises(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    source = FakeSource(records=(person("a"), person("b"), person("c")), name="fake", fail_after=1)

    with pytest.raises(RuntimeError, match="source exploded"):
        Scraper(source).fetch_to_bucket(bucket, run_id="run-1")

    manifest = bucket.read_manifest("fake", "run-1")
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.record_count == 1  # one record streamed before the failure
    assert manifest.error is not None
    assert "RuntimeError" in manifest.error


def test_iter_from_bucket_reads_back_typed_documents(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    docs = (
        person("Beethoven, Ludwig van", SourceClaim(predicate="born_on", value="1770-12-17")),
        mention("Symphony No. 5", "Beethoven, Ludwig van"),
    )
    run_id = Scraper(FakeSource(records=docs, name="fake")).fetch_to_bucket(bucket)

    restored = tuple(iter_from_bucket("fake", run_id, bucket))
    assert restored == docs
    assert isinstance(restored[0], EntityDocument)
    assert isinstance(restored[1], WorkMentionDocument)


def test_dump_to_bucket_shim_delegates_to_scraper(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    # dump_to_bucket is an intentionally-untyped back-compat shim.
    run_id = raw_fetch.dump_to_bucket(FakeSource(records=(person("a"),), name="fake"), bucket)
    assert bucket.read_manifest("fake", run_id) is not None
    assert len(list(bucket.read_records("fake", run_id))) == 1
