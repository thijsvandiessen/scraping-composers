from pathlib import Path

import httpx
import pytest
from composer_bronze.bucket import LocalBucket
from composer_crawler import CrawlConfig, Crawler, iter_crawl_records


def _config() -> CrawlConfig:
    return CrawlConfig(
        name="roundtrip",
        seeds=("https://example.org/",),
        follow_links=True,
        allow_patterns=(r"https://example\.org/",),
        request_delay_s=0.0,
        respect_robots=False,
    )


def _crawler(handler: httpx.MockTransport) -> Crawler:
    return Crawler(_config(), client=httpx.Client(transport=handler))


def test_crawl_to_bucket_roundtrip(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text='<a href="/a">a</a>', headers={"Content-Type": "text/html"})
        return httpx.Response(200, text="leaf", headers={"Content-Type": "text/html"})

    bucket = LocalBucket(tmp_path)
    run_id = _crawler(httpx.MockTransport(handler)).crawl_to_bucket(bucket)

    manifest = bucket.read_manifest("roundtrip", run_id)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.record_count == 2

    raw = list(bucket.read_records("roundtrip", run_id))
    assert all(record["_type"] == "crawl" for record in raw)

    records = list(iter_crawl_records("roundtrip", run_id, bucket))
    assert [r.url for r in records] == ["https://example.org/", "https://example.org/a"]
    assert records[0].body == '<a href="/a">a</a>'


def test_failed_crawl_writes_failed_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import composer_crawler._http

    monkeypatch.setattr(composer_crawler._http.time, "sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    bucket = LocalBucket(tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        _crawler(httpx.MockTransport(handler)).crawl_to_bucket(bucket, run_id="run-1")

    manifest = bucket.read_manifest("roundtrip", "run-1")
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.error is not None and "HTTPStatusError" in manifest.error
    assert manifest.record_count == 0
