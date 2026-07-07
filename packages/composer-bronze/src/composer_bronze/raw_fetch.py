"""Backward-compatible re-exports from scraper.py.

New code should import from :mod:`composer_bronze.scraper` directly.
"""

from .scraper import Scraper, iter_from_bucket

__all__ = ["Scraper", "iter_from_bucket"]


def dump_to_bucket(source, bucket, max_pages=None):  # type: ignore[no-untyped-def]
    return Scraper(source).fetch_to_bucket(bucket, max_pages=max_pages)
