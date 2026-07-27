"""Crawl configuration: what to discover and how to rank it for scraping.

A :class:`CrawlConfig` declares seed URLs plus discovery and relevance-ranking
rules; the :class:`~composer_crawler.crawler.Crawler` executes it with crawl4ai
without knowing anything about the target site. Discovery finds URLs (primarily
from ``sitemap.xml``); ranking orders them by relevance to ``relevance_query``
so the most important pages are scraped first.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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
    """Declarative description of one crawl target.

    The crawl runs in two phases: *discovery* collects candidate URLs (from the
    site's ``sitemap.xml`` by default, optionally Common Crawl), and *scraping*
    fetches them, rendered in a headless browser, in descending order of
    relevance to ``relevance_query``. When no query is set, URLs keep their
    discovery order.
    """

    name: str
    seeds: tuple[str, ...]
    # Discovery: sitemap.xml (and optionally Common Crawl) seeded from each seed's host.
    use_sitemap: bool = True
    use_common_crawl: bool = False
    # A glob (crawl4ai URL-seeder syntax) restricting which discovered URLs are kept.
    allow_patterns: tuple[str, ...] = ()
    # Ranking: BM25 over each URL's head metadata against this query; None keeps discovery order.
    relevance_query: str | None = None
    score_threshold: float = 0.0
    # When discovery yields nothing, follow links from the seeds up to this depth instead.
    follow_links: bool = False
    max_depth: int = 2
    # Cap on the number of URLs scraped (None: no cap).
    max_pages: int | None = None
    # CSS selector for elements dropped before markdown generation, added to the
    # consent-dialog selector every crawl already applies. Consent banners are dense
    # prose that survives the pruning filter and can dwarf a page's real content.
    excluded_selector: str | None = None
    request_delay_s: float = 0.5
    headers: tuple[tuple[str, str], ...] = ()
    respect_robots: bool = True
    timeout_s: float = 30.0
    # Which LLM schema the `extract` step applies to this crawl's pages:
    # "concerts" (default) or "recordings" (album/release listings).
    extract_kind: str = "concerts"

    def __post_init__(self) -> None:
        _validate_source_name(self.name)
        if not self.seeds:
            raise ValueError(f"crawl {self.name!r}: seeds must not be empty")
        if self.extract_kind not in ("concerts", "recordings"):
            raise ValueError(
                f"crawl {self.name!r}: extract_kind must be 'concerts' or 'recordings', "
                f"got {self.extract_kind!r}"
            )
        if self.follow_links and not self.allow_patterns:
            # An unrestricted link-following crawl would wander off the target host.
            raise ValueError(f"crawl {self.name!r}: follow_links requires at least one allow pattern")
        # allow_patterns are globs (crawl4ai URL-seeder / URLPatternFilter syntax), not regexes.
        for pattern in self.allow_patterns:
            if not pattern:
                raise ValueError(f"crawl {self.name!r}: allow patterns must be non-empty globs")
