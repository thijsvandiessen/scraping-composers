"""Registered crawl configurations.

Add a :class:`~composer_crawler.config.CrawlConfig` here to make it available
to the CLI's ``crawl`` command, mirroring how ``composer_scrapers.REGISTRY``
exposes source adapters to ``fetch``. Example::

    CRAWL_REGISTRY["example"] = CrawlConfig(
        name="example",
        seeds=("https://example.org/archive",),
        follow_links=True,
        allow_patterns=(r"https://example\\.org/archive/",),
    )
"""

from __future__ import annotations

from .config import CrawlConfig

CRAWL_REGISTRY: dict[str, CrawlConfig] = {}
