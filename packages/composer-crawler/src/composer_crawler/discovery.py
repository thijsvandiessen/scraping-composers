"""Phase one of a crawl: find candidate URLs and rank them by importance.

Wraps crawl4ai's :class:`AsyncUrlSeeder`, which discovers a site's URLs from its
``sitemap.xml`` (and optionally the Common Crawl index) and, given a query,
scores each one by BM25 over its head metadata. :func:`discover_urls` returns the
matching URLs ordered most-relevant first, so the crawler scrapes the important
pages before its budget runs out.
"""

from __future__ import annotations

import logging
import time
from fnmatch import fnmatch
from typing import Any
from urllib.parse import urlsplit

from crawl4ai import AsyncUrlSeeder, SeedingConfig

from .config import CrawlConfig

log = logging.getLogger(__name__)


def _source(config: CrawlConfig) -> str | None:
    """crawl4ai seeder source token, or None when discovery is disabled."""
    if config.use_sitemap and config.use_common_crawl:
        return "sitemap+cc"
    if config.use_sitemap:
        return "sitemap"
    if config.use_common_crawl:
        return "cc"
    return None


def _hits_per_sec(delay: float) -> int:
    """Politeness cap for the seeder derived from the per-request delay."""
    return max(1, round(1.0 / delay)) if delay > 0 else 5


def _seeding_config(config: CrawlConfig, source: str, budget: int | None) -> SeedingConfig:
    has_query = bool(config.relevance_query)
    # A single glob is pushed into the seeder; multiple are applied locally below.
    pattern = config.allow_patterns[0] if len(config.allow_patterns) == 1 else "*"
    # With a query we must rank the whole candidate set, then cap to max_pages
    # afterwards; capping here (max_urls) would truncate before ranking and drop
    # the most-relevant pages. Without a query the sitemap order stands, so the
    # cap can be pushed down — and it is the difference between seeding a few
    # dozen URLs and enumerating a 700k-URL site before a single page is fetched,
    # because the seeder stops its producer as soon as max_urls is reached.
    max_urls = -1 if has_query else (budget or -1)
    return SeedingConfig(
        source=source,
        pattern=pattern,
        extract_head=has_query,  # head metadata is what BM25 ranks over
        query=config.relevance_query,
        scoring_method="bm25",
        score_threshold=config.score_threshold if has_query else None,
        max_urls=max_urls,
        hits_per_sec=_hits_per_sec(config.request_delay_s),
    )


def _hosts(seeds: tuple[str, ...]) -> list[str]:
    """Unique seed hosts, preserving first-seen order."""
    ordered: dict[str, None] = {}
    for seed in seeds:
        if host := urlsplit(seed).netloc:
            ordered.setdefault(host, None)
    return list(ordered)


def _matches(url: str, patterns: tuple[str, ...]) -> bool:
    return not patterns or any(fnmatch(url, pattern) for pattern in patterns)


async def discover_urls(config: CrawlConfig, budget: int | None = None) -> list[str]:
    """Candidate URLs for *config*, ordered most-relevant first.

    Seeds each seed host's sitemap.xml (and optionally Common Crawl) through the
    crawl4ai URL seeder. With ``relevance_query`` set, results come back ranked
    by BM25; otherwise they keep sitemap order. Returns an empty list when
    discovery is disabled or finds nothing, so the caller can fall back to the
    seeds themselves.

    *budget* is how many pages the crawl will actually scrape (the run's
    ``max_pages``, falling back to the config's). Without a ranking query it is
    pushed into the seeder so enumeration stops there instead of walking the
    whole site first.
    """
    source = _source(config)
    if source is None:
        log.debug("crawl %r: discovery disabled (no sitemap, no common crawl)", config.name)
        return []
    if budget is None:
        budget = config.max_pages
    seeding = _seeding_config(config, source, budget)
    hosts = _hosts(config.seeds)
    started = time.monotonic()
    # INFO, not DEBUG: discovery precedes the first page by minutes on a large
    # site, and its silence next to an empty snapshot reads as a hung crawl.
    log.info(
        "crawl %r: discovering URLs from %d host(s) via %s (max_urls=%s)%s",
        config.name,
        len(hosts),
        source,
        "unlimited" if seeding.max_urls < 0 else seeding.max_urls,
        " — ranking the whole candidate set first" if config.relevance_query else "",
    )
    log.debug(
        "crawl %r: seeding pattern=%r, query=%r, threshold=%s",
        config.name,
        seeding.pattern,
        config.relevance_query,
        seeding.score_threshold,
    )
    async with AsyncUrlSeeder() as seeder:
        by_host = await seeder.many_urls(hosts, seeding)

    entries: list[dict[str, Any]] = [entry for results in by_host.values() for entry in results]
    log.debug("crawl %r: seeder returned %d raw entrie(s)", config.name, len(entries))
    if config.relevance_query:
        # Per-host results are ranked; a global sort re-merges them across hosts.
        entries.sort(key=lambda entry: entry.get("relevance_score", 0.0), reverse=True)

    urls: list[str] = []
    seen: set[str] = set()
    dropped = 0
    for entry in entries:
        url = entry.get("url")
        if not url or url in seen or not _matches(url, config.allow_patterns):
            dropped += 1
            continue
        seen.add(url)
        urls.append(url)
    log.debug(
        "crawl %r: %d entrie(s) dropped as duplicates or off-pattern (%s)",
        config.name,
        dropped,
        ", ".join(config.allow_patterns) or "no allow_patterns",
    )
    if budget is not None:
        urls = urls[:budget]
    log.info("crawl %r: discovered %d URL(s) in %.0fs", config.name, len(urls), time.monotonic() - started)
    return urls
