"""Generic crawl workflow, powered by crawl4ai.

The :class:`Crawler` executes a :class:`~composer_crawler.config.CrawlConfig` in
two phases: :func:`~composer_crawler.discovery.discover_urls` finds candidate
URLs (sitemap.xml first) and ranks them by relevance, then the pages are scraped
in that order — rendered in a headless browser — and yielded as raw
:class:`~composer_crawler.records.CrawlRecord`\\ s or written to a bronze bucket.
It knows nothing about the target site.

crawl4ai is async; :meth:`Crawler.crawl` drives it to completion with
``asyncio.run`` so the crawler presents the same synchronous surface as the rest
of the codebase (the CLI and the admin-api background task both call it from a
plain thread with no running event loop).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from typing import Any

from composer_bronze.bucket import Bucket, SnapshotManifest
from composer_bronze.scraper import new_snapshot_id

from . import fetch
from .config import CrawlConfig
from .discovery import discover_urls
from .records import CrawlRecord

log = logging.getLogger(__name__)

# Signature of the crawl4ai crawler factory (fetch.new_web_crawler); a seam for tests.
WebCrawlerFactory = Callable[[CrawlConfig], Any]


class Crawler:
    """Discover a target's URLs (sitemap-first), then scrape them most-important-first."""

    def __init__(self, config: CrawlConfig, web_crawler_factory: WebCrawlerFactory | None = None) -> None:
        self.config = config
        self._new_web_crawler = web_crawler_factory or fetch.new_web_crawler

    def crawl(self, max_pages: int | None = None) -> Iterator[CrawlRecord]:
        """Discover and scrape, yielding raw records ranked by relevance.

        *max_pages* overrides ``config.max_pages`` when given, capping the number
        of URLs scraped. Discovery falls back to the seeds (optionally following
        links) when no sitemap / Common-Crawl URLs are found.
        """
        return iter(asyncio.run(self._acrawl(max_pages)))

    async def _acrawl(self, max_pages: int | None) -> list[CrawlRecord]:
        budget = max_pages if max_pages is not None else self.config.max_pages
        urls = await discover_urls(self.config)
        deep_crawl = False
        if not urls:
            urls = list(self.config.seeds)
            deep_crawl = self.config.follow_links
        if budget is not None:
            urls = urls[:budget]
        return await self._scrape(urls, deep_crawl=deep_crawl, budget=budget)

    async def _scrape(self, urls: list[str], *, deep_crawl: bool, budget: int | None) -> list[CrawlRecord]:
        run = fetch.run_config(self.config, deep_crawl=deep_crawl, budget=budget)
        dispatcher = fetch.dispatcher(self.config)
        records: list[CrawlRecord] = []
        async with self._new_web_crawler(self.config) as crawler:
            results = await crawler.arun_many(urls, config=run, dispatcher=dispatcher)
            for result in results:
                record = fetch.record_from_result(result)
                if record is None:
                    continue
                records.append(record)
                if budget is not None and len(records) >= budget:
                    break
        return records

    def crawl_to_bucket(self, bucket: Bucket, max_pages: int | None = None, run_id: str | None = None) -> str:
        """Crawl and write all records to *bucket*, mirroring ``Scraper.fetch_to_bucket``.

        Writes a manifest alongside the records: ``running`` while the crawl
        streams, finalized to ``completed`` with the record count, or ``failed``
        with the error (the exception is re-raised). Returns the run_id for
        :func:`~composer_crawler.records.iter_crawl_records`.
        """
        if run_id is None:
            run_id = new_snapshot_id()
        manifest = SnapshotManifest.start(self.config.name, run_id)
        bucket.write_manifest(manifest)
        count = 0

        def counted() -> Iterator[dict[str, Any]]:
            nonlocal count
            for record in self.crawl(max_pages):
                yield record.to_dict()
                count += 1

        try:
            bucket.write_records(self.config.name, run_id, counted())
        except Exception as exc:
            bucket.write_manifest(manifest.failed(f"{type(exc).__name__}: {exc}", record_count=count))
            raise
        bucket.write_manifest(manifest.completed(record_count=count))
        return run_id
