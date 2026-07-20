"""Crawl configuration: what to fetch and how to discover more of it.

A :class:`CrawlConfig` declares seed URLs plus optional pagination and
link-following rules; the :class:`~composer_crawler.crawler.Crawler` executes
it without knowing anything about the target site or API.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NextUrlFromJson:
    """API pagination: follow the URL found at a dot-path in the JSON body.

    ``pointer`` is a dot-separated path of object keys, e.g. ``"pagination.next"``;
    a missing key or a null/non-string value ends the pagination.
    """

    pointer: str


@dataclass(frozen=True)
class PageParam:
    """Pagination by incrementing a query parameter (``?page=1``, ``?page=2``, ...).

    Stops when a page yields an empty body, a body identical to the previous
    page's, or an empty top-level JSON array.
    """

    param: str = "page"
    start: int = 1


Pagination = NextUrlFromJson | PageParam


def _validate_source_name(value: str) -> None:
    """Require a single path segment, matching the bucket's own guard (CWE-22).

    ``name`` becomes a directory under the bucket root; anything resembling
    traversal must be rejected before a crawl starts, not when it first writes.
    """
    if (
        not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or os.path.basename(value) != value
    ):
        raise ValueError(f"invalid crawl name {value!r}: must be a single path segment")


@dataclass(frozen=True)
class CrawlConfig:
    """Declarative description of one crawl target (a site or an API)."""

    name: str
    seeds: tuple[str, ...]
    follow_links: bool = False
    allow_patterns: tuple[str, ...] = ()
    max_depth: int = 2
    max_pages: int | None = None
    pagination: Pagination | None = None
    request_delay_s: float = 0.5
    headers: tuple[tuple[str, str], ...] = ()
    respect_robots: bool = True
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        _validate_source_name(self.name)
        if not self.seeds:
            raise ValueError(f"crawl {self.name!r}: seeds must not be empty")
        if self.follow_links and not self.allow_patterns:
            # An unrestricted frontier would wander off the target host.
            raise ValueError(f"crawl {self.name!r}: follow_links requires at least one allow pattern")
        for pattern in self.allow_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"crawl {self.name!r}: invalid allow pattern {pattern!r}: {exc}") from exc
