"""Tests for the page mirror.

The mirror exists so a source that spends one request per record pays for a
sweep once. Two things therefore matter more than the storage: that a stored
page is served instead of a request, and that a broken mirror costs a refetch
rather than the run.
"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from composer_http.pages import PageCache, open_page_cache, request_fingerprint

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


# ---- freshness: the cadence and the request ---- #


def _age(path: Path, url: str, days: int) -> None:
    """Backdate *url*'s row by *days*."""
    stamp = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE page_cache SET fetched_at = ? WHERE url = ?", (stamp, url))


def test_a_page_younger_than_the_max_age_is_served(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put(URL, "page")
    _age(tmp_path / "pages.db", URL, days=300)
    assert cache.get(URL, max_age=timedelta(days=365)) == "page"


def test_a_page_older_than_the_max_age_is_a_miss(tmp_path: Path) -> None:
    """The run that becomes due by the source's cadence must refetch."""
    cache = PageCache(tmp_path / "pages.db")
    cache.put(URL, "page")
    _age(tmp_path / "pages.db", URL, days=400)
    assert cache.get(URL, max_age=timedelta(days=365)) is None
    assert (cache.hits, cache.misses, cache.stale) == (0, 1, 1)
    assert "1 expired or changed" in cache.summary()


def test_without_a_max_age_a_page_never_expires(tmp_path: Path) -> None:
    """A STATIC source — an archive, scraped once — keeps its mirror forever."""
    cache = PageCache(tmp_path / "pages.db")
    cache.put(URL, "page")
    _age(tmp_path / "pages.db", URL, days=10_000)
    assert cache.get(URL) == "page"


def test_a_page_stored_by_a_different_request_is_a_miss(tmp_path: Path) -> None:
    """Changing the query in code must not keep serving the old query's answer."""
    cache = PageCache(tmp_path / "pages.db")
    old = request_fingerprint("POST", URL, '{"query": "{ a }"}')
    new = request_fingerprint("POST", URL, '{"query": "{ a b }"}')
    cache.put(URL, "answer to the old query", fingerprint=old)
    assert cache.get(URL, fingerprint=old) == "answer to the old query"
    assert cache.get(URL, fingerprint=new) is None
    assert cache.stale == 1


def test_a_page_stored_without_a_fingerprint_does_not_answer_one(tmp_path: Path) -> None:
    """A row from before fingerprints cannot say which query produced it."""
    cache = PageCache(tmp_path / "pages.db")
    cache.put(URL, "page")
    assert cache.get(URL, fingerprint=request_fingerprint("POST", URL, "{}")) is None


def test_the_fingerprint_follows_every_part_of_the_request() -> None:
    base = request_fingerprint("POST", URL, "body")
    assert base == request_fingerprint("post", URL, "body")
    assert base == request_fingerprint("POST", URL, b"body")
    assert base != request_fingerprint("GET", URL, "body")
    assert base != request_fingerprint("POST", URL + "?x", "body")
    assert base != request_fingerprint("POST", URL, "body ")


def test_a_mirror_from_before_fingerprints_is_upgraded_in_place(tmp_path: Path) -> None:
    """The archive mirrors are hours of fetching; a schema change must keep them."""
    path = tmp_path / "pages.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE page_cache (url TEXT PRIMARY KEY, body BLOB NOT NULL, fetched_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO page_cache VALUES (?, ?, ?)",
            (URL, gzip.compress(b"old page"), datetime.now(UTC).isoformat()),
        )
    cache = PageCache(path)
    assert cache.get(URL) == "old page"
    cache.put("https://example.org/concert/2/", "new", fingerprint="f")
    assert cache.get("https://example.org/concert/2/", fingerprint="f") == "new"
