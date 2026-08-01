"""Generic crawl workflow, powered by crawl4ai.

The :class:`Crawler` executes a :class:`~composer_crawler.config.CrawlConfig` in
two phases: :func:`~composer_crawler.discovery.discover_urls` finds candidate
URLs (sitemap.xml first) and ranks them by relevance, then the pages are scraped
in that order — rendered in a headless browser — and yielded as raw
:class:`~composer_crawler.records.CrawlRecord`\\ s or written to a bronze bucket.
It knows nothing about the target site.

crawl4ai is async; :meth:`Crawler.crawl` pulls its pages one at a time through
an :class:`asyncio.Runner`, so the crawler presents the same synchronous —
and *streaming* — surface as the rest of the codebase (the CLI and the
admin-api background task both call it from a plain thread with no running
event loop). Streaming rather than returning a finished list is what lets
:meth:`Crawler.crawl_to_bucket` append each page to the snapshot as it arrives:
these crawls run for tens of minutes unattended, and a killed one used to lose
everything it had fetched.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Generator, Iterator
from contextlib import aclosing
from typing import Any

from composer_bronze.bucket import Bucket, SnapshotManifest
from composer_bronze.scraper import new_snapshot_id

from . import fetch
from .config import CrawlConfig
from .discovery import discover_urls
from .progress import CrawlProgress
from .records import CrawlRecord

log = logging.getLogger(__name__)

# Signature of the crawl4ai crawler factory (fetch.new_web_crawler); a seam for tests.
WebCrawlerFactory = Callable[[CrawlConfig], Any]


class Crawler:
    """Discover a target's URLs (sitemap-first), then scrape them most-important-first."""

    def __init__(self, config: CrawlConfig, web_crawler_factory: WebCrawlerFactory | None = None) -> None:
        self.config = config
        self._new_web_crawler = web_crawler_factory or fetch.new_web_crawler

    def crawl(self, max_pages: int | None = None) -> Generator[CrawlRecord, None, None]:
        """Discover and scrape, yielding raw records ranked by relevance.

        *max_pages* overrides ``config.max_pages`` when given, capping the number
        of URLs scraped. Discovery falls back to the seeds (optionally following
        links) when no sitemap / Common-Crawl URLs are found.

        Pages are yielded as they finish, not collected first: a caller that
        writes them out keeps whatever the crawl managed before an interruption.
        One :class:`asyncio.Runner` drives the whole crawl, so the browser
        session and crawl4ai's own stream stay open across the pulls — and are
        closed by the ``finally`` even when the consumer walks away early.
        """
        with asyncio.Runner() as runner:
            records = self._acrawl(max_pages)
            try:
                while True:
                    try:
                        record = runner.run(records.__anext__())
                    except StopAsyncIteration:
                        return
                    yield record
            finally:
                runner.run(records.aclose())

    async def _acrawl(self, max_pages: int | None) -> AsyncGenerator[CrawlRecord, None]:
        budget = max_pages if max_pages is not None else self.config.max_pages
        log.info(
            "crawl %r: starting from %d seed(s), budget=%s",
            self.config.name,
            len(self.config.seeds),
            budget if budget is not None else "unlimited",
        )
        urls = await discover_urls(self.config, budget)
        deep_crawl = False
        if not urls:
            urls = list(self.config.seeds)
            deep_crawl = self.config.follow_links
            log.info(
                "crawl %r: discovery found nothing, falling back to the seeds (follow_links=%s)",
                self.config.name,
                deep_crawl,
            )
        if budget is not None:
            urls = urls[:budget]
        async for record in self._scrape(urls, deep_crawl=deep_crawl, budget=budget):
            yield record

    async def _scrape(
        self, urls: list[str], *, deep_crawl: bool, budget: int | None
    ) -> AsyncGenerator[CrawlRecord, None]:
        run = fetch.run_config(self.config, deep_crawl=deep_crawl, budget=budget)
        dispatcher = fetch.dispatcher(self.config)
        progress = CrawlProgress(self.config.name, len(urls))
        scraped = 0
        # finally, so an abandoned or crashed crawl still reports how far it got;
        # its tally is the only account of an unattended run.
        try:
            async with self._new_web_crawler(self.config) as crawler:
                results = await crawler.arun_many(urls, config=run, dispatcher=dispatcher)
                # aclosing, because breaking on the budget leaves a streamed generator
                # suspended mid-crawl; it has to be told to shut down.
                async with aclosing(fetch.aiter_results(results)) as stream:
                    async for result in stream:
                        record = fetch.record_from_result(result)
                        if record is None:
                            progress.mark_skipped(result.url)
                            continue
                        scraped += 1
                        progress.mark_page(record)
                        yield record
                        if budget is not None and scraped >= budget:
                            break
        finally:
            progress.finish()

    def crawl_to_bucket(self, bucket: Bucket, max_pages: int | None = None, run_id: str | None = None) -> str:
        """Crawl and write all records to *bucket*, mirroring ``Scraper.fetch_to_bucket``.

        Records are appended as :meth:`crawl` yields them, so the snapshot on
        disk tracks the crawl instead of appearing only at the end; a run that
        is killed leaves the pages it did fetch (its manifest stays ``running``,
        which is how a stale snapshot is recognized).

        Writes a manifest alongside the records: ``running`` while the crawl
        streams, finalized to ``completed`` with the record count, or ``failed``
        with the error (the exception is re-raised). Returns the run_id for
        :func:`~composer_crawler.records.iter_crawl_records`.
        """
        if run_id is None:
            run_id = new_snapshot_id()
        log.info("crawl %r: writing snapshot %s", self.config.name, run_id)
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
            log.warning(
                "crawl %r: snapshot %s failed after %d record(s): %s: %s",
                self.config.name,
                run_id,
                count,
                type(exc).__name__,
                exc,
            )
            bucket.write_manifest(manifest.failed(f"{type(exc).__name__}: {exc}", record_count=count))
            raise
        bucket.write_manifest(manifest.completed(record_count=count))
        log.info("crawl %r: snapshot %s complete, %d record(s)", self.config.name, run_id, count)
        return run_id
