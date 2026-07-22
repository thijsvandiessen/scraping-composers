"""crawl_to_bucket writes ranked records plus a manifest, and marks a crashed
crawl failed — driven by a fake crawl4ai crawler, no browser or network."""

from __future__ import annotations

from pathlib import Path

import composer_crawler.crawler as crawler_mod
import pytest
from composer_bronze.bucket import LocalBucket
from composer_crawler import CrawlConfig, Crawler, iter_crawl_records
from composer_crawler.testing import FakeResult, FakeWebCrawler, stub_discover, web_crawler_factory


def _config() -> CrawlConfig:
    return CrawlConfig(name="roundtrip", seeds=("https://example.org/",), request_delay_s=0.0)


def test_crawl_to_bucket_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls = ["https://example.org/", "https://example.org/a"]
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(urls))
    results = {
        url: FakeResult(url, html=f"<p>{url}</p>", response_headers={"Content-Type": "text/html"})
        for url in urls
    }
    fake = FakeWebCrawler(results)
    bucket = LocalBucket(tmp_path)

    run_id = Crawler(_config(), web_crawler_factory=web_crawler_factory(fake)).crawl_to_bucket(bucket)

    manifest = bucket.read_manifest("roundtrip", run_id)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.record_count == 2

    raw = list(bucket.read_records("roundtrip", run_id))
    assert all(record["_type"] == "crawl" for record in raw)

    records = list(iter_crawl_records("roundtrip", run_id, bucket))
    assert [r.url for r in records] == urls  # stored in discovery (relevance) order
    assert records[0].body == "<p>https://example.org/</p>"


def test_failed_crawl_writes_failed_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(["https://example.org/"]))
    fake = FakeWebCrawler(fail=RuntimeError("connection torn down"))
    bucket = LocalBucket(tmp_path)

    with pytest.raises(RuntimeError):
        Crawler(_config(), web_crawler_factory=web_crawler_factory(fake)).crawl_to_bucket(
            bucket, run_id="run-1"
        )

    manifest = bucket.read_manifest("roundtrip", "run-1")
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.error is not None and "RuntimeError" in manifest.error
    assert manifest.record_count == 0
