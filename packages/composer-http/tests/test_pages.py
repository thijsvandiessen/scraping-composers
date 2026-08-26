"""Tests for the page mirror.

The mirror exists so a source that spends one request per record pays for a
sweep once. Two things therefore matter more than the storage: that a stored
page is served instead of a request, and that a broken mirror costs a refetch
rather than the run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from composer_http.pages import PageCache, open_page_cache

URL = "https://example.org/concert/1/"


def test_a_stored_page_comes_back(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put(URL, "<html>Küchенmusik</html>")
    assert cache.get(URL) == "<html>Küchенmusik</html>"


def test_a_page_never_fetched_is_a_miss(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    assert cache.get(URL) is None
    assert (cache.hits, cache.misses) == (0, 1)


def test_hits_and_misses_are_counted(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put(URL, "page")
    assert cache.get(URL) == "page"
    assert cache.get("https://example.org/concert/2/") is None
    assert (cache.hits, cache.misses) == (1, 1)
    assert cache.summary() == "1 mirrored, 1 fetched (50% of requests saved)"


def test_storing_a_url_twice_replaces_it(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put(URL, "before")
    cache.put(URL, "after")
    assert cache.get(URL) == "after"


def test_the_mirror_outlives_the_object_that_wrote_it(tmp_path: Path) -> None:
    PageCache(tmp_path / "pages.db").put(URL, "page")
    assert PageCache(tmp_path / "pages.db").get(URL) == "page"


def test_a_corrupt_body_reads_as_a_miss(tmp_path: Path) -> None:
    # a mirror is an optimization: a page that cannot be read is refetched
    path = tmp_path / "pages.db"
    cache = PageCache(path)
    cache.put(URL, "page")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE page_cache SET body = ?", (b"not gzip",))
    assert PageCache(path).get(URL) is None


def test_an_unusable_file_does_not_end_the_run(tmp_path: Path) -> None:
    unusable = tmp_path / "pages.db"
    unusable.write_text("this is not a database")
    cache = PageCache(unusable)
    cache.put(URL, "page")  # must not raise
    assert cache.get(URL) is None


def test_open_page_cache_honours_the_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "page_cache_path", str(tmp_path / "pages.db"))
    monkeypatch.setattr(settings, "page_cache_enabled", False)
    assert open_page_cache() is None

    monkeypatch.setattr(settings, "page_cache_enabled", True)
    cache = open_page_cache()
    assert cache is not None
    assert cache.path == tmp_path / "pages.db"
