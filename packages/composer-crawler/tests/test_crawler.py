"""Crawler tests: discovery is stubbed and the crawl4ai crawler is a fake, so
the scrape phase runs with no browser or network."""

from __future__ import annotations

import json
import logging
from typing import Any

import composer_crawler.crawler as crawler_mod
import pytest
from composer_crawler import CrawlConfig, Crawler, CrawlRecord
from composer_crawler.testing import (
    FakeMarkdown,
    FakeResult,
    FakeStringCompatibleMarkdown,
    FakeWebCrawler,
    StreamingFakeWebCrawler,
    stub_discover,
    web_crawler_factory,
)


def _config(**overrides: Any) -> CrawlConfig:
    base: dict[str, Any] = {"name": "site", "seeds": ("https://example.org/",), "request_delay_s": 0.0}
    base.update(overrides)
    return CrawlConfig(**base)


def _run(
    config: CrawlConfig,
    fake: FakeWebCrawler,
    monkeypatch: pytest.MonkeyPatch,
    *,
    discovered: list[str],
    max_pages: int | None = None,
) -> list[Any]:
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(discovered))
    crawler = Crawler(config, web_crawler_factory=web_crawler_factory(fake))
    return list(crawler.crawl(max_pages))


def test_scrapes_discovered_urls_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = ["https://example.org/a", "https://example.org/b", "https://example.org/c"]
    fake = FakeWebCrawler()
    records = _run(_config(), fake, monkeypatch, discovered=urls)
    assert [r.url for r in records] == urls
    assert all(r.depth == 0 for r in records)
    assert fake.scraped_urls == urls


def test_max_pages_caps_urls_scraped(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = ["https://example.org/a", "https://example.org/b", "https://example.org/c"]
    fake = FakeWebCrawler()
    records = _run(_config(), fake, monkeypatch, discovered=urls, max_pages=2)
    assert [r.url for r in records] == urls[:2]
    assert fake.scraped_urls == urls[:2]  # the budget is applied before scraping


def test_record_maps_crawl4ai_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    result = FakeResult(
        url="https://example.org/x",
        html="<p>hi</p>",
        status_code=200,
        response_headers={"Content-Type": "text/html; charset=utf-8", "ETag": '"abc"'},
        redirected_url="https://example.org/x/final",
        markdown=FakeMarkdown(raw_markdown="# hi", fit_markdown="hi"),
        metadata={"title": "Concert", "description": "programme", "depth": 0},
    )
    fake = FakeWebCrawler({result.url: result})
    (record,) = _run(_config(), fake, monkeypatch, discovered=[result.url])
    assert record.content_type == "text/html"
    assert record.headers["etag"] == '"abc"'  # header names are lowercased
    assert record.final_url == "https://example.org/x/final"
    assert record.markdown == "hi"  # prefers fit_markdown over raw_markdown
    assert record.metadata == {"title": "Concert", "description": "programme"}  # depth dropped
    assert record.fetched_at  # ISO timestamp present


def test_record_markdown_falls_back_to_raw_when_no_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    result = FakeResult(url="https://example.org/y", markdown=FakeMarkdown(raw_markdown="# raw"))
    fake = FakeWebCrawler({result.url: result})
    (record,) = _run(_config(), fake, monkeypatch, discovered=[result.url])
    assert record.markdown == "# raw"
    assert record.metadata == {}  # no page metadata present


def test_markdown_prefers_fit_over_the_str_value_of_crawl4ais_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """crawl4ai's wrapper *is* a str whose value is the unpruned raw_markdown.

    Reading it as a string would store the far larger unpruned text, and the
    wrapper does not survive ``asdict`` — so the record must hold a plain str.
    """
    markdown = FakeStringCompatibleMarkdown(FakeMarkdown(raw_markdown="# raw " * 100, fit_markdown="fit"))
    result = FakeResult(url="https://example.org/z", markdown=markdown)
    fake = FakeWebCrawler({result.url: result})
    (record,) = _run(_config(), fake, monkeypatch, discovered=[result.url])
    assert record.markdown == "fit"
    assert type(record.markdown) is str
    json.dumps(record.to_dict())  # the bucket writes NDJSON; must not raise


def test_legacy_record_with_html_body_still_loads() -> None:
    """Snapshots crawled before records dropped ``body`` must not break loading."""
    record = CrawlRecord.from_dict(
        {
            "_type": "crawl",
            "url": "https://example.org/x",
            "final_url": "https://example.org/x",
            "status_code": 200,
            "content_type": "text/html",
            "fetched_at": "2024-01-01T00:00:00+00:00",
            "depth": 0,
            "headers": {},
            "body": "<p>dropped</p>",
        }
    )
    assert record.url == "https://example.org/x"
    assert record.markdown == ""


def test_hard_failure_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    good = "https://example.org/a"
    dead = "https://example.org/dead"
    fake = FakeWebCrawler({dead: FakeResult(dead, html="", success=False, error_message="boom")})
    records = _run(_config(), fake, monkeypatch, discovered=[good, dead])
    assert [r.url for r in records] == [good]


def test_missing_status_code_becomes_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.org/a"
    fake = FakeWebCrawler({url: FakeResult(url, status_code=None, html="x")})
    (record,) = _run(_config(), fake, monkeypatch, discovered=[url])
    assert record.status_code == 0


def test_falls_back_to_seeds_and_deep_crawl_when_discovery_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(follow_links=True, allow_patterns=("*example.org*",))
    fake = FakeWebCrawler()
    records = _run(config, fake, monkeypatch, discovered=[])
    assert fake.scraped_urls == list(config.seeds)
    assert fake.run_config.deep_crawl_strategy is not None  # link-following enabled
    assert [r.url for r in records] == list(config.seeds)


def test_no_deep_crawl_when_discovery_yields_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeWebCrawler()
    _run(
        _config(follow_links=True, allow_patterns=("*",)),
        fake,
        monkeypatch,
        discovered=["https://example.org/a"],
    )
    assert fake.run_config.deep_crawl_strategy is None  # discovered URLs need no link-following


def test_pages_are_asked_for_as_a_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Progress reporting is only worth anything if the pages arrive one at a time
    rather than as one batch once the whole crawl has already finished."""
    fake = FakeWebCrawler()
    _run(_config(), fake, monkeypatch, discovered=["https://example.org/a"])
    assert fake.run_config.stream is True


def test_a_streamed_crawl_yields_the_same_records(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = ["https://example.org/a", "https://example.org/b"]
    fake = StreamingFakeWebCrawler()
    records = _run(_config(), fake, monkeypatch, discovered=urls)
    assert [r.url for r in records] == urls
    assert fake.streamed == 2


def test_a_streamed_crawl_is_closed_when_the_budget_cuts_it_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaking out of the stream leaves the generator suspended mid-crawl unless
    it is closed, which would hold the browser session open."""
    urls = ["https://example.org/a", "https://example.org/b", "https://example.org/c"]
    fake = StreamingFakeWebCrawler()
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover(urls))
    crawler = Crawler(_config(max_pages=None), web_crawler_factory=web_crawler_factory(fake))

    records = list(crawler.crawl(2))

    assert len(records) == 2
    assert fake.closed is True


def test_the_crawl_reports_progress_and_a_summary(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeWebCrawler()
    with caplog.at_level(logging.INFO, logger="composer_crawler"):
        _run(_config(), fake, monkeypatch, discovered=["https://example.org/a"])

    messages = [r.getMessage() for r in caplog.records]
    assert any("starting from 1 seed(s)" in m for m in messages)
    assert any("finished" in m and "1 pages" in m for m in messages)


def test_falling_back_to_the_seeds_is_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Discovery finding nothing is the most confusing way for a crawl to come back
    near-empty, so it must not be silent."""
    fake = FakeWebCrawler()
    with caplog.at_level(logging.INFO, logger="composer_crawler.crawler"):
        _run(_config(follow_links=True, allow_patterns=("*",)), fake, monkeypatch, discovered=[])

    assert any("falling back to the seeds" in r.getMessage() for r in caplog.records)
