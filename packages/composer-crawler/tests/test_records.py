"""The digest a crawl stamps on each page, and reading it back off old snapshots."""

from __future__ import annotations

from pathlib import Path

from composer_bronze.bucket import LocalBucket, SnapshotManifest
from composer_crawler.records import (
    CrawlRecord,
    content_hash,
    prior_content_hashes,
    record_content_hash,
)


def _record(url: str = "https://example.org/a", markdown: str = "page a") -> CrawlRecord:
    return CrawlRecord(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        fetched_at="2026-08-01T00:00:00+00:00",
        depth=0,
        headers={},
        markdown=markdown,
        content_sha256=content_hash(markdown),
    )


def test_the_same_text_always_hashes_the_same() -> None:
    assert content_hash("page a") == content_hash("page a")
    assert content_hash("page a") != content_hash("page b")


def test_surrounding_whitespace_is_not_a_change() -> None:
    """The extract stage strips the markdown before reading it, so a page that only
    gained a trailing newline must not read as changed and re-enter the model."""
    assert content_hash("  page a\n\n") == content_hash("page a")


def test_a_record_from_an_older_snapshot_still_yields_a_hash() -> None:
    """Snapshots outlive the schema: records written before the field existed carry
    an empty digest and are hashed on the fly rather than compared as equal."""
    legacy = CrawlRecord(
        url="https://example.org/a",
        final_url="https://example.org/a",
        status_code=200,
        content_type="text/html",
        fetched_at="2026-07-01T00:00:00+00:00",
        depth=0,
        headers={},
        markdown="page a",
    )

    assert legacy.content_sha256 == ""
    assert record_content_hash(legacy) == content_hash("page a")


def test_a_legacy_record_without_the_field_still_loads() -> None:
    payload = _record().to_dict()
    del payload["content_sha256"]

    assert CrawlRecord.from_dict(payload).content_sha256 == ""


def test_prior_hashes_come_from_the_last_complete_snapshot(tmp_path: Path) -> None:
    bucket = LocalBucket(tmp_path)
    bucket.write_records("site", "run-1", [_record(markdown="old").to_dict()])
    bucket.write_manifest(SnapshotManifest.start("site", "run-1").completed(1))

    assert prior_content_hashes("site", bucket) == {"https://example.org/a": content_hash("old")}


def test_an_unfinished_snapshot_is_not_used_as_the_baseline(tmp_path: Path) -> None:
    """A crawl that is still running (or crashed) is a partial view of the site;
    comparing against it would report most of the next crawl as changed."""
    bucket = LocalBucket(tmp_path)
    bucket.write_records("site", "run-1", [_record().to_dict()])
    bucket.write_manifest(SnapshotManifest.start("site", "run-1"))

    assert prior_content_hashes("site", bucket) == {}


def test_a_source_with_no_snapshots_yet_compares_against_nothing(tmp_path: Path) -> None:
    assert prior_content_hashes("site", LocalBucket(tmp_path)) == {}
