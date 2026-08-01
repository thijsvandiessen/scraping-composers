"""discover_urls tests: the crawl4ai URL seeder is a fake, so no network runs."""

from __future__ import annotations

from typing import Any

import composer_crawler.discovery as discovery_mod
import pytest
from composer_crawler import CrawlConfig
from composer_crawler.discovery import discover_urls
from composer_crawler.testing import FakeSeeder


def _entry(url: str, score: float | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"url": url, "status": "valid", "head_data": {}}
    if score is not None:
        entry["relevance_score"] = score
    return entry


def _install_seeder(monkeypatch: pytest.MonkeyPatch, by_host: dict[str, list[dict[str, Any]]]) -> FakeSeeder:
    seeder = FakeSeeder(by_host)
    monkeypatch.setattr(discovery_mod, "AsyncUrlSeeder", lambda *a, **k: seeder)
    return seeder


def _run(config: CrawlConfig, budget: int | None = None) -> list[str]:
    import asyncio

    return asyncio.run(discover_urls(config, budget))


def test_ranks_by_relevance_across_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_seeder(
        monkeypatch,
        {
            "a.example": [_entry("https://a.example/1", 0.2), _entry("https://a.example/2", 0.9)],
            "b.example": [_entry("https://b.example/1", 0.5)],
        },
    )
    config = CrawlConfig(
        name="c",
        seeds=("https://a.example/", "https://b.example/"),
        relevance_query="composer biography",
    )
    assert _run(config) == [
        "https://a.example/2",  # 0.9
        "https://b.example/1",  # 0.5
        "https://a.example/1",  # 0.2
    ]


def test_keeps_discovery_order_without_query(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_seeder(
        monkeypatch,
        {"a.example": [_entry("https://a.example/1"), _entry("https://a.example/2")]},
    )
    config = CrawlConfig(name="c", seeds=("https://a.example/",))
    assert _run(config) == ["https://a.example/1", "https://a.example/2"]


def test_filters_by_allow_pattern_and_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_seeder(
        monkeypatch,
        {
            "a.example": [
                _entry("https://a.example/composer/x"),
                _entry("https://a.example/work/y"),
                _entry("https://a.example/composer/x"),  # duplicate
            ]
        },
    )
    config = CrawlConfig(name="c", seeds=("https://a.example/",), allow_patterns=("*/composer/*",))
    assert _run(config) == ["https://a.example/composer/x"]


def test_caps_at_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_seeder(
        monkeypatch,
        {"a.example": [_entry(f"https://a.example/{i}") for i in range(5)]},
    )
    config = CrawlConfig(name="c", seeds=("https://a.example/",), max_pages=3)
    assert len(_run(config)) == 3


def test_the_page_budget_stops_the_seeder_rather_than_the_result_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap has to reach the seeder: enumerating a large site's whole URL set
    and slicing afterwards costs minutes before the first page is ever fetched."""
    seeder = _install_seeder(monkeypatch, {"a.example": [_entry("https://a.example/1")]})
    config = CrawlConfig(name="c", seeds=("https://a.example/",))

    _run(config, budget=20)

    assert seeder.config.max_urls == 20


def test_the_run_budget_wins_over_the_configs_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    seeder = _install_seeder(monkeypatch, {"a.example": [_entry(f"https://a.example/{i}") for i in range(5)]})
    config = CrawlConfig(name="c", seeds=("https://a.example/",), max_pages=4)

    assert len(_run(config, budget=2)) == 2
    assert seeder.config.max_urls == 2


def test_an_unbudgeted_crawl_still_falls_back_to_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    seeder = _install_seeder(monkeypatch, {"a.example": [_entry("https://a.example/1")]})
    config = CrawlConfig(name="c", seeds=("https://a.example/",), max_pages=3)

    _run(config)

    assert seeder.config.max_urls == 3


def test_a_ranked_crawl_still_seeds_everything_before_capping(monkeypatch: pytest.MonkeyPatch) -> None:
    """BM25 has to score the whole candidate set; capping the seeder would
    truncate before ranking and drop the most-relevant pages."""
    seeder = _install_seeder(
        monkeypatch,
        {"a.example": [_entry(f"https://a.example/{i}", score=float(i)) for i in range(5)]},
    )
    config = CrawlConfig(name="c", seeds=("https://a.example/",), relevance_query="composer")

    assert len(_run(config, budget=2)) == 2  # capped locally, after ranking
    assert seeder.config.max_urls == -1  # but never at the seeder


def test_disabled_discovery_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    seeder = _install_seeder(monkeypatch, {"a.example": [_entry("https://a.example/1")]})
    config = CrawlConfig(name="c", seeds=("https://a.example/",), use_sitemap=False, use_common_crawl=False)
    assert _run(config) == []
    assert seeder.config is None  # the seeder was never consulted
