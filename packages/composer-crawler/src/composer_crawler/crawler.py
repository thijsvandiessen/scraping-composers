"""Generic crawl workflow.

The :class:`Crawler` executes a :class:`~composer_crawler.config.CrawlConfig`:
it fetches the seeds, optionally paginates them and follows discovered links,
and yields raw :class:`~composer_crawler.records.CrawlRecord`\\ s or writes
them to a bronze bucket. It knows nothing about the target site or API.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from composer_bronze.bucket import Bucket, SnapshotManifest
from composer_bronze.scraper import new_snapshot_id

from ._http import call_with_retries, user_agent
from .config import CrawlConfig, NextUrlFromJson, PageParam
from .frontier import Frontier, extract_links, normalize_url
from .records import CrawlRecord, kept_headers
from .robots import RobotsCache

log = logging.getLogger(__name__)

_TEXT_TYPES = ("application/json", "application/xml")


def _normalize_content_type(raw: str | None) -> str | None:
    """Media type without parameters, lowercased (``"text/html; charset=x"`` → ``"text/html"``)."""
    if raw is None:
        return None
    return raw.split(";", 1)[0].strip().lower() or None


def _is_text(content_type: str | None) -> bool:
    # A missing Content-Type is assumed textual; binary responses normally label themselves.
    if content_type is None:
        return True
    return (
        content_type.startswith("text/")
        or content_type in _TEXT_TYPES
        or content_type.endswith(("+json", "+xml"))
    )


def _walk_json_path(body: str, pointer: str) -> str | None:
    """The string at dot-path *pointer* in the JSON *body*, or None."""
    try:
        data: Any = json.loads(body)
    except ValueError:
        return None
    for key in pointer.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data if isinstance(data, str) else None


def _without_param(url: str, param: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _with_param(url: str, param: str, value: int) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    query.append((param, str(value)))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _current_page(url: str, param: str, start: int) -> int:
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key == param and value.lstrip("-").isdigit():
            return int(value)
    return start


def _make_client(config: CrawlConfig) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent(), **dict(config.headers)},
        timeout=config.timeout_s,
        follow_redirects=True,
    )


def _get(client: httpx.Client, url: str) -> httpx.Response:
    """GET that raises only for retryable statuses (429 and 5xx); other 4xx are recorded as-is."""
    response = client.get(url)
    if response.status_code == 429 or response.status_code >= 500:
        response.raise_for_status()
    return response


def _pop_allowed(frontier: Frontier, robots: RobotsCache | None) -> tuple[str, int] | None:
    """The next fetchable frontier entry, skipping robots-disallowed URLs;
    None when the frontier is exhausted."""
    while (item := frontier.pop()) is not None:
        if robots is not None and not robots.allowed(item[0]):
            log.info("robots.txt disallows %s; skipping", item[0])
            continue
        return item
    return None


def _record_from_response(response: httpx.Response, url: str, depth: int) -> CrawlRecord | None:
    """A CrawlRecord for the response, or None for non-text bodies."""
    content_type = _normalize_content_type(response.headers.get("Content-Type"))
    if not _is_text(content_type):
        log.warning("skipping non-text body at %s (%s)", url, content_type)
        return None
    return CrawlRecord(
        url=url,
        final_url=str(response.url),
        status_code=response.status_code,
        content_type=content_type,
        fetched_at=datetime.now(UTC).isoformat(),
        depth=depth,
        body=response.text,
        headers=kept_headers(response.headers),
    )


class Crawler:
    """Generic crawl workflow; the target is described entirely by the config."""

    def __init__(self, config: CrawlConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client
        self._patterns = tuple(re.compile(p) for p in config.allow_patterns)
        self._seed_urls = {normalize_url(seed) for seed in config.seeds}

    def crawl(self, max_pages: int | None = None) -> Iterator[CrawlRecord]:
        """Fetch seeds (plus paginated and discovered pages) and yield raw records.

        *max_pages* overrides ``config.max_pages`` when given; it caps the
        total number of requests. A seed that still fails after retries
        aborts the crawl; failures on discovered or paginated URLs are
        logged and skipped.
        """
        budget = max_pages if max_pages is not None else self.config.max_pages
        client = self._client if self._client is not None else _make_client(self.config)
        try:
            robots = RobotsCache(client) if self.config.respect_robots else None
            frontier = Frontier()
            for seed in self.config.seeds:
                frontier.add(seed, 0)
            prev_pages: dict[str, str] = {}
            fetched = 0
            while budget is None or fetched < budget:
                item = _pop_allowed(frontier, robots)
                if item is None:
                    break
                url, depth = item
                if fetched:
                    time.sleep(self.config.request_delay_s)
                response = self._fetch(client, url, depth)
                if response is None:
                    continue
                fetched += 1
                record = _record_from_response(response, url, depth)
                if record is None:
                    continue
                yield record
                self._enqueue_followups(record, frontier, prev_pages)
        finally:
            if self._client is None:
                client.close()

    def _fetch(self, client: httpx.Client, url: str, depth: int) -> httpx.Response | None:
        """GET with retries. A seed that still fails aborts the crawl; other
        failures return None so the URL is skipped."""
        try:
            return call_with_retries(partial(_get, client, url), label=url)
        except httpx.HTTPError as exc:
            if depth == 0 and url in self._seed_urls:
                raise
            log.warning("skipping %s after retries (%s)", url, exc)
            return None

    def _enqueue_followups(self, record: CrawlRecord, frontier: Frontier, prev_pages: dict[str, str]) -> None:
        """Queue the next page and any allowed discovered links after a success."""
        if record.status_code >= 400:
            return
        depth = record.depth
        if depth == 0 and self.config.pagination is not None:
            self._enqueue_next_page(record, frontier, prev_pages)
        if self.config.follow_links and depth < self.config.max_depth and record.content_type == "text/html":
            for link in extract_links(record.body, record.final_url):
                if any(pattern.search(link) for pattern in self._patterns):
                    frontier.add(link, depth + 1)

    def _enqueue_next_page(self, record: CrawlRecord, frontier: Frontier, prev_pages: dict[str, str]) -> None:
        pagination = self.config.pagination
        if isinstance(pagination, NextUrlFromJson):
            next_url = _walk_json_path(record.body, pagination.pointer)
            if next_url:
                frontier.add(urljoin(record.final_url, next_url), 0)
            return
        assert isinstance(pagination, PageParam)
        body = record.body
        if not body.strip():
            return
        if record.content_type is not None and "json" in record.content_type:
            try:
                if json.loads(body) == []:
                    return
            except ValueError:
                pass
        chain = _without_param(record.url, pagination.param)
        if prev_pages.get(chain) == body:
            return
        prev_pages[chain] = body
        current = _current_page(record.url, pagination.param, pagination.start)
        frontier.add(_with_param(record.url, pagination.param, current + 1), 0)

    def crawl_to_bucket(self, bucket: Bucket, max_pages: int | None = None, run_id: str | None = None) -> str:
        """Crawl and write all records to *bucket*, mirroring ``Scraper.fetch_to_bucket``.

        Writes a manifest alongside the records: ``running`` while the crawl
        streams, finalized to ``completed`` with the record count, or
        ``failed`` with the error (the exception is re-raised). Returns the
        run_id for :func:`~composer_crawler.records.iter_crawl_records`.
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
