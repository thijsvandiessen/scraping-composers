"""crawl4ai plumbing: build its configs and map its results to bronze records.

Keeps the crawl4ai-specific construction (browser, run, dispatcher, and the
link-following fallback strategy) out of :mod:`composer_crawler.crawler`, and
turns each :class:`CrawlResult` into the :class:`~composer_crawler.records.CrawlRecord`
the bronze bucket already stores.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from crawl4ai import (
    AsyncWebCrawler,
    BestFirstCrawlingStrategy,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    FilterChain,
    KeywordRelevanceScorer,
    MemoryAdaptiveDispatcher,
    PruningContentFilter,
    RateLimiter,
    URLPatternFilter,
)

from ._http import user_agent
from .config import CrawlConfig
from .records import CrawlRecord, content_hash, kept_headers

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


def _markdown_generator() -> DefaultMarkdownGenerator:
    """Generate main-content markdown: a pruning filter drops boilerplate so the
    result's ``fit_markdown`` is the compact input the LLM extraction step reads."""
    return DefaultMarkdownGenerator(content_filter=PruningContentFilter())


# Containers of the common consent-management platforms. Their dialogs are dense
# prose, so the pruning filter keeps them and they can outweigh the page itself
# (on lso.co.uk: 24 KB of banner around 5 KB of concert).
_CONSENT_SELECTOR = (
    "#CybotCookiebotDialog, #CookiebotWidget, #onetrust-consent-sdk, "
    "#usercentrics-root, #didomi-host, .qc-cmp2-container"
)


def _excluded_selector(config: CrawlConfig) -> str:
    """The consent-dialog selector plus the config's own additions."""
    if not config.excluded_selector:
        return _CONSENT_SELECTOR
    return f"{_CONSENT_SELECTOR}, {config.excluded_selector}"


def run_config(config: CrawlConfig, *, deep_crawl: bool, budget: int | None) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        check_robots_txt=config.respect_robots,
        mean_delay=config.request_delay_s,
        page_timeout=int(config.timeout_s * 1000),
        cache_mode=CacheMode.BYPASS,
        markdown_generator=_markdown_generator(),
        excluded_selector=_excluded_selector(config),
        remove_overlay_elements=True,
        deep_crawl_strategy=_deep_crawl_strategy(config, budget) if deep_crawl else None,
        # Hand back each page as it finishes instead of one list at the end, so a
        # long crawl can report progress while it is still running.
        stream=True,
        verbose=False,
    )


async def aiter_results(results: Any) -> AsyncGenerator[Any, None]:
    """Iterate crawl4ai's results whether they stream or arrive as one batch.

    ``arun_many`` returns an async generator under ``stream=True`` and a plain
    container otherwise; both shapes are worth supporting, so neither the
    streaming switch nor a test double that returns a list has to care. Typed as
    a generator rather than an iterator because the caller closes it explicitly
    when the page budget cuts a stream short.
    """
    if hasattr(results, "__aiter__"):
        async for result in results:
            yield result
        return
    for result in results:
        yield result


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


def _markdown(result: Any) -> str:
    """The result's pruned main-content markdown, as a plain :class:`str`.

    crawl4ai hands back a ``StringCompatibleMarkdown``: a ``str`` subclass whose
    *string value* is the unfiltered ``raw_markdown``, with the pruned
    ``fit_markdown`` reachable only as an attribute. So the attributes are tried
    first — an ``isinstance(str)`` shortcut would silently store the ~10x larger
    unpruned text. The result is coerced to a plain ``str`` because
    ``dataclasses.asdict`` deep-copies that subclass back into a
    ``MarkdownGenerationResult``, which is not JSON-serializable.
    """
    md = getattr(result, "markdown", None)
    if md is None:
        return ""
    for attribute in ("fit_markdown", "raw_markdown"):
        value = getattr(md, attribute, None)
        if value:
            return str(value)
    return str(md) if isinstance(md, str) else ""


def _page_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    """Page metadata (title, description, og:*, ...) as strings; ``depth`` is
    crawl bookkeeping, not page metadata, so it is left out."""
    return {str(k): str(v) for k, v in metadata.items() if k != "depth" and v is not None}


def record_from_result(result: Any) -> CrawlRecord | None:
    """A bronze CrawlRecord for a crawl4ai result, or None for a hard failure."""
    if not result.success and not result.html:
        log.warning("skipping %s (%s)", result.url, result.error_message)
        return None
    headers = {name.lower(): value for name, value in (result.response_headers or {}).items()}
    metadata = result.metadata or {}
    markdown = _markdown(result)
    return CrawlRecord(
        url=result.url,
        final_url=result.redirected_url or result.url,
        status_code=result.status_code or 0,
        content_type=_content_type(headers),
        fetched_at=datetime.now(UTC).isoformat(),
        depth=int(metadata.get("depth", 0)),
        headers=kept_headers(headers),
        markdown=markdown,
        metadata=_page_metadata(metadata),
        content_sha256=content_hash(markdown),
    )
