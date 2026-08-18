"""Bronze storage tests: LocalBucket filesystem IO plus the SnapshotManifest
state machine and its legacy-snapshot fallback."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from composer_bronze.bucket import (
    MANIFEST_FILENAME,
    LocalBucket,
    Snapshot,
    SnapshotManifest,
    all_document_run_ids,
    all_page_run_ids,
    latest_document_run_id,
    latest_loadable_run_id,
)


def test_manifest_start_is_running() -> None:
    m = SnapshotManifest.start("rco", "run-1")
    assert m.source == "rco"
    assert m.run_id == "run-1"
    assert m.status == "running"
    assert m.started_at  # ISO 8601 timestamp
    assert m.finished_at is None
    assert m.record_count is None
    assert m.error is None


def test_manifest_completed_records_count_and_finish() -> None:
    m = SnapshotManifest.start("rco", "run-1").completed(record_count=42)
    assert m.status == "completed"
    assert m.record_count == 42
    assert m.finished_at is not None
    assert m.error is None
    # start() is immutable; a fresh manifest is returned each transition
    assert SnapshotManifest.start("rco", "run-1").status == "running"


def test_manifest_failed_carries_error_and_partial_count() -> None:
    m = SnapshotManifest.start("rco", "run-1").failed("RuntimeError: boom", record_count=3)
    assert m.status == "failed"
    assert m.error == "RuntimeError: boom"
    assert m.record_count == 3
    assert m.finished_at is not None


def test_records_round_trip_preserves_unicode(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    records = [{"_type": "entity", "name": "Antonín Dvořák"}, {"_type": "work_mention", "title": "Requiem"}]
    bucket.write_records("rco", "run-1", records)

    # ensure_ascii=False keeps non-ASCII readable on disk
    raw = (tmp_path / "rco" / "run-1" / "records.ndjson").read_text(encoding="utf-8")
    assert "Dvořák" in raw

    assert list(bucket.read_records("rco", "run-1")) == records


def test_read_records_skips_a_malformed_trailing_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A run killed outright mid-flush can leave a truncated last line; it
    must not cost every record read before it."""
    run_dir = tmp_path / "rco" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "records.ndjson").write_text(
        '{"_type": "entity", "name": "A"}\n{"_type": "entity", "name": "B"}\n{"_type": "entity", "nam'
    )

    bucket = LocalBucket(tmp_path)
    with caplog.at_level("WARNING"):
        records = list(bucket.read_records("rco", "run-1"))

    assert records == [{"_type": "entity", "name": "A"}, {"_type": "entity", "name": "B"}]
    assert "malformed line 3" in caplog.text


def test_read_records_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "rco" / "run-1" / "records.ndjson"
    path.parent.mkdir(parents=True)
    path.write_text('{"a": 1}\n\n   \n{"b": 2}\n', encoding="utf-8")

    bucket = LocalBucket(tmp_path)
    assert list(bucket.read_records("rco", "run-1")) == [{"a": 1}, {"b": 2}]


def test_manifest_round_trip(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    manifest = SnapshotManifest.start("rco", "run-1").completed(record_count=7)
    bucket.write_manifest(manifest)
    assert bucket.read_manifest("rco", "run-1") == manifest


def test_read_manifest_returns_none_when_absent(tmp_path: Path) -> None:
    assert LocalBucket(tmp_path).read_manifest("rco", "missing") is None


def test_list_runs_empty_when_source_dir_missing(tmp_path: Path) -> None:
    assert LocalBucket(tmp_path).list_runs("rco") == []


def test_list_runs_returns_sorted_run_ids(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    for run_id in ("run-c", "run-a", "run-b"):
        bucket.write_records("rco", run_id, [{"x": 1}])
    assert bucket.list_runs("rco") == ["run-a", "run-b", "run-c"]


def test_list_snapshots_reports_manifest_and_size(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    bucket.write_records("rco", "run-1", [{"x": 1}])
    manifest = SnapshotManifest.start("rco", "run-1").completed(record_count=1)
    bucket.write_manifest(manifest)

    snapshots = bucket.list_snapshots("rco")
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert isinstance(snap, Snapshot)
    assert snap.manifest == manifest
    assert snap.size_bytes > 0


def _complete(bucket: LocalBucket, source: str, run_id: str, record: dict[str, object]) -> None:
    bucket.write_records(source, run_id, [record])
    bucket.write_manifest(SnapshotManifest.start(source, run_id).completed(record_count=1))


def _failed(bucket: LocalBucket, source: str, run_id: str, record: dict[str, object]) -> None:
    """A crashed run: the record was flushed before the crash, so it's still on disk."""
    bucket.write_records(source, run_id, [record])
    bucket.write_manifest(SnapshotManifest.start(source, run_id).failed("RuntimeError: boom", record_count=1))


def _running(bucket: LocalBucket, source: str, run_id: str, record: dict[str, object]) -> None:
    bucket.write_records(source, run_id, [record])
    bucket.write_manifest(SnapshotManifest.start(source, run_id))


def test_list_snapshots_classifies_kind_by_first_record(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    _complete(bucket, "lso", "run-pages", {"_type": "crawl", "url": "https://x"})
    _complete(bucket, "lso", "run-docs", {"_type": "entity", "id": "person:x"})
    _complete(bucket, "lso", "run-empty", {})  # no _type -> not loadable

    kinds = {s.manifest.run_id: s.kind for s in bucket.list_snapshots("lso")}
    assert kinds == {"run-pages": "pages", "run-docs": "documents", "run-empty": "pages"}


def test_list_sources_enumerates_bucket_dirs(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    bucket.write_records("wikidata", "r1", [{"x": 1}])
    bucket.write_records("lso", "r1", [{"x": 1}])
    (tmp_path / "not-a-dir.txt").write_text("ignored")
    assert bucket.list_sources() == ["lso", "wikidata"]
    assert LocalBucket(tmp_path / "missing").list_sources() == []


def test_latest_document_run_id_skips_pages(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    _complete(bucket, "lso", "2026-01-01T00:00:00-docs", {"_type": "work_mention", "title": "x"})
    _complete(bucket, "lso", "2026-02-01T00:00:00-pages", {"_type": "crawl", "url": "https://x"})

    # The pages crawl is the newest loadable snapshot, but not a documents one.
    assert latest_loadable_run_id(bucket, "lso") == "2026-02-01T00:00:00-pages"
    assert latest_document_run_id(bucket, "lso") == "2026-01-01T00:00:00-docs"
    assert latest_document_run_id(bucket, "missing") is None


def test_all_document_run_ids_includes_failed_excludes_running_and_pages(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    _complete(bucket, "henle", "2026-01-01T00:00:00-docs-1", {"_type": "entity", "id": "1"})
    _failed(bucket, "henle", "2026-01-02T00:00:00-docs-2", {"_type": "entity", "id": "2"})
    _complete(bucket, "henle", "2026-01-03T00:00:00-docs-3", {"_type": "entity", "id": "3"})
    _running(bucket, "henle", "2026-01-04T00:00:00-docs-4", {"_type": "entity", "id": "4"})
    _complete(bucket, "henle", "2026-01-05T00:00:00-pages", {"_type": "crawl", "url": "https://x"})

    assert all_document_run_ids(bucket, "henle") == [
        "2026-01-01T00:00:00-docs-1",
        "2026-01-02T00:00:00-docs-2",
        "2026-01-03T00:00:00-docs-3",
    ]
    assert all_document_run_ids(bucket, "missing") == []


def test_all_page_run_ids_includes_failed_excludes_running_and_documents(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    _complete(bucket, "lso", "2026-01-01T00:00:00-pages-1", {"_type": "crawl", "url": "https://x/1"})
    _failed(bucket, "lso", "2026-01-02T00:00:00-pages-2", {"_type": "crawl", "url": "https://x/2"})
    _complete(bucket, "lso", "2026-01-03T00:00:00-pages-3", {"_type": "crawl", "url": "https://x/3"})
    _running(bucket, "lso", "2026-01-04T00:00:00-pages-4", {"_type": "crawl", "url": "https://x/4"})
    _complete(bucket, "lso", "2026-01-05T00:00:00-docs", {"_type": "entity", "id": "1"})

    assert all_page_run_ids(bucket, "lso") == [
        "2026-01-01T00:00:00-pages-1",
        "2026-01-02T00:00:00-pages-2",
        "2026-01-03T00:00:00-pages-3",
    ]
    assert all_page_run_ids(bucket, "missing") == []


def test_list_snapshots_synthesizes_manifest_for_legacy_dir(tmp_path: Path) -> None:
    # A snapshot dir with records but no manifest predates the manifest feature.
    bucket = LocalBucket(tmp_path)
    bucket.write_records("rco", "legacy", [{"x": 1}])
    assert not (tmp_path / "rco" / "legacy" / MANIFEST_FILENAME).exists()

    (snap,) = bucket.list_snapshots("rco")
    assert snap.manifest.status == "unknown"
    assert snap.manifest.source == "rco"
    assert snap.manifest.run_id == "legacy"
    # started_at is synthesized from the records file mtime
    assert snap.manifest.started_at
    parsed = datetime.fromisoformat(snap.manifest.started_at)
    ndjson = tmp_path / "rco" / "legacy" / "records.ndjson"
    expected = datetime.fromtimestamp(os.stat(ndjson).st_mtime, tz=UTC)
    assert parsed == expected


@pytest.mark.parametrize(
    "bad_segment",
    [
        "",
        ".",
        "..",
        "../other",
        "..\\other",
        "nested/run-1",
        "/etc/passwd",
        "run\x00id",
        # Not traversal, but these segments reach log lines and terminals: a
        # newline forges a second log entry, ESC starts an escape sequence.
        "run\nid",
        "run\r\nid",
        "run\tid",
        "run\x1b[31mid",
        "run\x7fid",
    ],
)
def test_path_traversal_segments_rejected(tmp_path: Path, bad_segment: str) -> None:
    # source and run_id may come from untrusted input (API path params, CLI
    # args); anything that is not a single path segment must never touch disk.
    bucket = LocalBucket(tmp_path)
    with pytest.raises(ValueError, match="single path segment"):
        bucket.write_records("rco", bad_segment, [{"x": 1}])
    with pytest.raises(ValueError, match="single path segment"):
        list(bucket.read_records(bad_segment, "run-1"))
    with pytest.raises(ValueError, match="single path segment"):
        bucket.list_runs(bad_segment)
    with pytest.raises(ValueError, match="single path segment"):
        bucket.read_manifest("rco", bad_segment)
    with pytest.raises(ValueError, match="single path segment"):
        bucket.write_manifest(SnapshotManifest.start(bad_segment, "run-1"))


def test_a_real_run_id_keeps_its_colons(tmp_path: Path) -> None:
    """Run ids are ISO timestamps (``2026-07-02T09:52:30-3086f07d``), so the segment
    guard rejects control characters rather than allowlisting a charset — an
    allowlist without ``:`` would reject every snapshot already on disk.
    """
    bucket = LocalBucket(tmp_path)
    run_id = "2026-07-02T09:52:30-3086f07d"

    bucket.write_records("rco", run_id, [{"x": 1}])
    bucket.write_manifest(SnapshotManifest.start("rco", run_id))

    assert bucket.list_runs("rco") == [run_id]
    assert list(bucket.read_records("rco", run_id)) == [{"x": 1}]


def test_traversal_run_id_cannot_escape_bucket_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    root = tmp_path / "bucket"
    bucket = LocalBucket(root)
    with pytest.raises(ValueError):
        bucket.write_records("rco", "../../outside", [{"x": 1}])
    assert not outside.exists()


def test_manifest_serialized_as_flat_json(tmp_path: Path) -> None:
    # read_manifest reconstructs from a plain dict, so the on-disk shape matters.
    bucket = LocalBucket(tmp_path)
    bucket.write_manifest(SnapshotManifest.start("rco", "run-1"))
    payload = json.loads((tmp_path / "rco" / "run-1" / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert payload["source"] == "rco"
    assert payload["status"] == "running"
