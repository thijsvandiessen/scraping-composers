"""Crawler tests: discovery is stubbed and the crawl4ai crawler is a fake, so
the scrape phase runs with no browser or network."""

from __future__ import annotations

from typing import Any

import composer_crawler.crawler as crawler_mod
import pytest
from composer_crawler import CrawlConfig, Crawler
from composer_crawler.testing import FakeResult, FakeWebCrawler, stub_discover, web_crawler_factory


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
    )
    fake = FakeWebCrawler({result.url: result})
    (record,) = _run(_config(), fake, monkeypatch, discovered=[result.url])
    assert record.content_type == "text/html"
    assert record.headers["etag"] == '"abc"'  # header names are lowercased
    assert record.final_url == "https://example.org/x/final"
    assert record.body == "<p>hi</p>"
    assert record.fetched_at  # ISO timestamp present


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
