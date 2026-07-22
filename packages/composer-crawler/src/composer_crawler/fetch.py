"""crawl4ai plumbing: build its configs and map its results to bronze records.

Keeps the crawl4ai-specific construction (browser, run, dispatcher, and the
link-following fallback strategy) out of :mod:`composer_crawler.crawler`, and
turns each :class:`CrawlResult` into the :class:`~composer_crawler.records.CrawlRecord`
the bronze bucket already stores.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from crawl4ai import (
    AsyncWebCrawler,
    BestFirstCrawlingStrategy,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    FilterChain,
    KeywordRelevanceScorer,
    MemoryAdaptiveDispatcher,
    RateLimiter,
    URLPatternFilter,
)

from ._http import user_agent
from .config import CrawlConfig
from .records import CrawlRecord, kept_headers

log = logging.getLogger(__name__)


def browser_config(config: CrawlConfig) -> BrowserConfig:
    """Headless Chromium identified by our contact User-Agent."""
    return BrowserConfig(
        headless=True,
        user_agent=user_agent(),
        headers=dict(config.headers),
        verbose=False,
    )


def _deep_crawl_strategy(config: CrawlConfig, budget: int | None) -> BestFirstCrawlingStrategy:
    """Link-following fallback: stay within allow_patterns, visit relevant URLs first."""
    filters = FilterChain([URLPatternFilter(patterns=list(config.allow_patterns), use_glob=True)])
    scorer = (
        KeywordRelevanceScorer(keywords=config.relevance_query.split()) if config.relevance_query else None
    )
    kwargs: dict[str, Any] = {"max_depth": config.max_depth, "filter_chain": filters, "url_scorer": scorer}
    if budget is not None:
        kwargs["max_pages"] = budget
    return BestFirstCrawlingStrategy(**kwargs)


def run_config(config: CrawlConfig, *, deep_crawl: bool, budget: int | None) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        check_robots_txt=config.respect_robots,
        mean_delay=config.request_delay_s,
        page_timeout=int(config.timeout_s * 1000),
        cache_mode=CacheMode.BYPASS,
        deep_crawl_strategy=_deep_crawl_strategy(config, budget) if deep_crawl else None,
        verbose=False,
    )


def dispatcher(config: CrawlConfig) -> MemoryAdaptiveDispatcher:
    delay = config.request_delay_s
    return MemoryAdaptiveDispatcher(rate_limiter=RateLimiter(base_delay=(delay, delay * 2)))


def new_web_crawler(config: CrawlConfig) -> AsyncWebCrawler:
    """The crawl4ai crawler for *config*; a seam tests replace with a fake."""
    return AsyncWebCrawler(config=browser_config(config))


def _content_type(headers: dict[str, str]) -> str:
    raw = headers.get("content-type")
    if not raw:
        return "text/html"  # a browser render is always HTML even when the header is absent
    return raw.split(";", 1)[0].strip().lower() or "text/html"


def record_from_result(result: Any) -> CrawlRecord | None:
    """A bronze CrawlRecord for a crawl4ai result, or None for a hard failure."""
    if not result.success and not result.html:
        log.warning("skipping %s (%s)", result.url, result.error_message)
        return None
    headers = {name.lower(): value for name, value in (result.response_headers or {}).items()}
    metadata = result.metadata or {}
    return CrawlRecord(
        url=result.url,
        final_url=result.redirected_url or result.url,
        status_code=result.status_code or 0,
        content_type=_content_type(headers),
        fetched_at=datetime.now(UTC).isoformat(),
        depth=int(metadata.get("depth", 0)),
        body=result.html,
        headers=kept_headers(headers),
    )
