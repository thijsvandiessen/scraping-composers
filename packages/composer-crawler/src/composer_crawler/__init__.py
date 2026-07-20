"""Generic crawler for web pages and API endpoints.

Separate from the per-source adapters in ``composer_scrapers``: a crawl is
described declaratively by a :class:`CrawlConfig` (seeds, pagination,
link-following rules) and executed by the generic :class:`Crawler`, which
stores raw responses in the bronze bucket without any parsing.
"""

from .config import CrawlConfig, NextUrlFromJson, PageParam, Pagination
from .crawler import Crawler
from .records import CrawlRecord, iter_crawl_records
from .registry import CRAWL_REGISTRY
from .store import CrawlConfigStore, all_crawl_configs, config_from_dict, config_to_dict

__all__ = [
    "CRAWL_REGISTRY",
    "CrawlConfig",
    "CrawlConfigStore",
    "CrawlRecord",
    "Crawler",
    "NextUrlFromJson",
    "PageParam",
    "Pagination",
    "all_crawl_configs",
    "config_from_dict",
    "config_to_dict",
    "iter_crawl_records",
]
