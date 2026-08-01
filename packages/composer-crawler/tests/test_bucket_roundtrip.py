"""crawl_to_bucket writes ranked records plus a manifest, and marks a crashed
crawl failed — driven by a fake crawl4ai crawler, no browser or network."""

from __future__ import annotations

from pathlib import Path

import composer_crawler.crawler as crawler_mod
import pytest
from composer_bronze.bucket import LocalBucket
from composer_crawler import CrawlConfig, Crawler, iter_crawl_records
from composer_crawler.testing import (
    FakeMarkdown,
    FakeResult,
    FakeWebCrawler,
    StreamingFakeWebCrawler,
    stub_discover,
    web_crawler_factory,
)


def _config() -> CrawlConfig:
    return CrawlConfig(name="roundtrip", seeds=("https://example.org/",), request_delay_s=0.0)


def test_crawl_to_bucket_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls = ["https://example.org/", "https://example.org/a"]
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(urls))
    results = {
        url: FakeResult(
            url,
            html=f"<p>{url}</p>",
            markdown=FakeMarkdown(fit_markdown=f"page {url}"),
            response_headers={"Content-Type": "text/html"},
        )
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
    assert records[0].markdown == "page https://example.org/"  # markdown, not the source HTML
    assert "body" not in raw[0]


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


def test_a_crawl_that_dies_partway_keeps_the_pages_it_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason records stream to the bucket: an unattended crawl of a large
    site used to lose every page it had fetched when it was interrupted."""
    urls = [f"https://example.org/{n}" for n in range(5)]
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(urls))
    fake = StreamingFakeWebCrawler(fail_after=3)
    bucket = LocalBucket(tmp_path)

    with pytest.raises(RuntimeError):
        Crawler(_config(), web_crawler_factory=web_crawler_factory(fake)).crawl_to_bucket(
            bucket, run_id="run-2"
        )

    records = list(iter_crawl_records("roundtrip", "run-2", bucket))
    assert [r.url for r in records] == urls[:3]

    manifest = bucket.read_manifest("roundtrip", "run-2")
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.record_count == 3  # the manifest agrees with what is on disk


def test_a_re_crawl_reports_which_pages_did_not_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crawling a site again writes a fresh snapshot, but most of it is usually
    the same text. The tally is what says how much of the run was worth doing —
    and unchanged pages are exactly the ones the extract cache will serve free.
    """
    urls = ["https://example.org/a", "https://example.org/b"]
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(urls))
    bucket = LocalBucket(tmp_path)

    def crawler_for(body_b: str) -> Crawler:
        results = {
            "https://example.org/a": FakeResult(
                "https://example.org/a", html="<p>a</p>", markdown=FakeMarkdown(fit_markdown="page a")
            ),
            "https://example.org/b": FakeResult(
                "https://example.org/b", html="<p>b</p>", markdown=FakeMarkdown(fit_markdown=body_b)
            ),
        }
        return Crawler(_config(), web_crawler_factory=web_crawler_factory(FakeWebCrawler(results)))

    crawler_for("page b").crawl_to_bucket(bucket, run_id="run-1")
    second = crawler_for("page b, revised")
    second.crawl_to_bucket(bucket, run_id="run-2")

    first_records = {r.final_url: r for r in iter_crawl_records("roundtrip", "run-1", bucket)}
    second_records = {r.final_url: r for r in iter_crawl_records("roundtrip", "run-2", bucket)}
    assert first_records["https://example.org/a"].content_sha256 == (
        second_records["https://example.org/a"].content_sha256
    )
    assert first_records["https://example.org/b"].content_sha256 != (
        second_records["https://example.org/b"].content_sha256
    )


def test_the_first_crawl_of_a_source_has_nothing_to_compare_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(["https://example.org/a"]))
    bucket = LocalBucket(tmp_path)

    Crawler(_config(), web_crawler_factory=web_crawler_factory(StreamingFakeWebCrawler())).crawl_to_bucket(
        bucket, run_id="run-1"
    )

    records = list(iter_crawl_records("roundtrip", "run-1", bucket))
    assert len(records) == 1
    assert records[0].content_sha256 != ""
