"""Generic crawler for web pages, powered by crawl4ai.

Separate from the per-source adapters in ``composer_scrapers``: a crawl is
described declaratively by a :class:`CrawlConfig` (seeds, discovery and
relevance-ranking rules) and executed by the generic :class:`Crawler`, which
discovers URLs (sitemap.xml first), scrapes them most-relevant-first in a
headless browser, and stores the raw responses in the bronze bucket without any
parsing.
"""

from .config import CrawlConfig
from .crawler import Crawler
from .discovery import discover_urls
from .progress import CrawlProgress, CrawlStats
from .records import CrawlRecord, iter_crawl_records
from .registry import CRAWL_REGISTRY
from .store import CrawlConfigStore, all_crawl_configs, config_from_dict, config_to_dict

__all__ = [
    "CRAWL_REGISTRY",
    "CrawlConfig",
    "CrawlConfigStore",
    "CrawlProgress",
    "CrawlRecord",
    "CrawlStats",
    "Crawler",
    "all_crawl_configs",
    "config_from_dict",
    "config_to_dict",
    "discover_urls",
    "iter_crawl_records",
]
