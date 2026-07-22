"""Test doubles for driving :class:`~composer_crawler.crawler.Crawler` without
crawl4ai or the network.

Both this package's tests and the admin-api's crawl-endpoint tests share these:
:class:`FakeWebCrawler` stands in for a crawl4ai ``AsyncWebCrawler`` (inject it
via ``Crawler(config, web_crawler_factory=...)``), and :func:`stub_discover`
replaces :func:`composer_crawler.discovery.discover_urls` with a fixed URL list.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any


@dataclass
class FakeResult:
    """Stand-in for a crawl4ai ``CrawlResult`` (only the fields the mapper reads)."""

    url: str
    html: str = "<html>ok</html>"
    success: bool = True
    status_code: int | None = 200
    response_headers: dict[str, str] | None = None
    redirected_url: str | None = None
    metadata: dict[str, Any] | None = None
    error_message: str | None = None


class FakeWebCrawler:
    """Async-context web crawler that returns canned results and records its inputs.

    ``results`` maps a URL to the :class:`FakeResult` to return for it; unmapped
    URLs get a default success result. Pass ``fail`` to have ``arun_many`` raise.
    """

    def __init__(
        self, results: dict[str, FakeResult] | None = None, *, fail: Exception | None = None
    ) -> None:
        self._results = results or {}
        self._fail = fail
        self.scraped_urls: list[str] = []
        self.run_config: Any = None

    async def __aenter__(self) -> FakeWebCrawler:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def arun_many(
        self, urls: list[str], config: Any = None, dispatcher: Any = None
    ) -> list[FakeResult]:
        if self._fail is not None:
            raise self._fail
        self.scraped_urls = list(urls)
        self.run_config = config
        return [self._results.get(url, FakeResult(url)) for url in urls]


def web_crawler_factory(crawler: FakeWebCrawler) -> Callable[[Any], FakeWebCrawler]:
    """A ``Crawler`` web-crawler factory that always yields *crawler*."""
    return lambda _config: crawler


def stub_discover(urls: list[str]) -> Callable[[Any], Coroutine[Any, Any, list[str]]]:
    """An async ``discover_urls`` replacement returning a fixed URL list."""

    async def _discover(_config: Any) -> list[str]:
        return list(urls)

    return _discover


class FakeSeeder:
    """Async-context URL seeder returning canned per-host entries (for discovery tests)."""

    def __init__(self, by_host: dict[str, list[dict[str, Any]]]) -> None:
        self._by_host = by_host
        self.config: Any = None

    async def __aenter__(self) -> FakeSeeder:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def many_urls(self, domains: Any, config: Any) -> dict[str, list[dict[str, Any]]]:
        self.config = config
        return {domain: self._by_host.get(domain, []) for domain in domains}
