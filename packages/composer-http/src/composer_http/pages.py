"""Remember pages a scraper already fetched, so an archive is fetched once.

Most sources here are a dozen requests; a few are one request *per record*. The
Vienna Philharmonic concert archive is the extreme: 10,749 detail pages, one per
concert, which at a polite request rate is a three-hour sweep. That sweep is
worth paying once and never again — the archive is a historical record, so a
page fetched today says the same thing next month.

So this is a mirror of the source as of its last scheduled run, and two things
decide whether a stored page is still served:

* **Age, coupled to the source's cadence.** A lookup may pass ``max_age`` — the
  adapter's :class:`~composer_schema.RefreshCadence` interval — and a page older
  than that is a miss. A run that becomes due therefore refetches, while a run
  interrupted and resumed inside the interval reuses everything it already got.
  A ``STATIC`` source (an archive, scraped once) passes no age and its pages are
  served forever.
* **The request that produced it.** A lookup may pass a ``fingerprint`` of the
  request definition (a POST body, a GraphQL selection, a SPARQL query) and a
  page stored under a different one is a miss, so editing a query in code
  invalidates what the old query returned. A plain GET passes none: its URL
  already *is* the whole request.

Deleting the file is the hard reset, and ``PAGE_CACHE_ENABLED=false`` bypasses it
for a run.

Bodies are gzipped, which matters at this scale: the Vienna archive's pages are
~36KB each and compress to ~10KB, so the whole mirror is ~100MB rather than
~380MB.

Modelled on :mod:`composer_extract.cache`, including its failure policy: a cache
is an optimization and never a reason to fail, so every SQLite error degrades to
"not cached" and is logged.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS page_cache (
    url         TEXT PRIMARY KEY,
    body        BLOB NOT NULL,
    fetched_at  TEXT NOT NULL,
    fingerprint TEXT
)
"""


def request_fingerprint(method: str, url: str, body: str | bytes | None = None) -> str:
    """A stable digest of a request definition, for :meth:`PageCache.get`.

    Two requests that would ask the server the same question share a
    fingerprint; changing the method, the URL or a single byte of the body
    changes it.
    """
    digest = hashlib.sha256()
    for part in (method.upper(), url):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    if body is not None:
        digest.update(body.encode("utf-8") if isinstance(body, str) else body)
    return digest.hexdigest()


@dataclass
class PageCache:
    """SQLite-backed store of fetched pages, keyed by URL.

    Connections are opened per operation and closed again, and the file is in
    WAL mode, so a fetch running in the admin API's background task and one
    running from the CLI can share the mirror.
    """

    path: Path
    hits: int = 0
    misses: int = 0
    #: Misses that found a row but refused it: too old, or a different request.
    stale: int = 0
    _ready: bool = field(default=False, repr=False)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        if not self._ready:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(_SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(page_cache)")}
            if "fingerprint" not in columns:
                # a mirror written before fingerprints existed: its rows are all
                # plain GETs, which carry no fingerprint, so they stay valid
                connection.execute("ALTER TABLE page_cache ADD COLUMN fingerprint TEXT")
            connection.commit()
            self._ready = True
        return connection

    def get(
        self, url: str, *, fingerprint: str | None = None, max_age: timedelta | None = None
    ) -> str | None:
        """The stored body for *url*, or None when there is no usable one.

        A row stored under a different *fingerprint*, or fetched more than
        *max_age* ago, is a miss (and counted as :attr:`stale`).
        """
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT body, fetched_at, fingerprint FROM page_cache WHERE url = ?", (url,)
                ).fetchone()
            if row is not None and not _fresh(row[1], row[2], fingerprint, max_age):
                self.stale += 1
                row = None
            body = gzip.decompress(row[0]).decode("utf-8") if row is not None else None
        except (sqlite3.Error, OSError, EOFError, UnicodeDecodeError, ValueError) as exc:
            # a corrupt row is as good as a missing one: refetch beats crashing
            log.warning("page cache: lookup failed (%s: %s); treating as a miss", type(exc).__name__, exc)
            return None
        if body is None:
            self.misses += 1
            return None
        self.hits += 1
        return body

    def put(self, url: str, body: str, *, fingerprint: str | None = None) -> None:
        """Store *body* for *url*, as produced by the request *fingerprint*;
        an existing row is replaced."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO page_cache (url, body, fetched_at, fingerprint) "
                    "VALUES (?, ?, ?, ?)",
                    (url, gzip.compress(body.encode("utf-8")), datetime.now(UTC).isoformat(), fingerprint),
                )
        except (sqlite3.Error, OSError) as exc:
            log.warning("page cache: write failed (%s: %s); continuing uncached", type(exc).__name__, exc)

    def summary(self) -> str:
        """What the mirror saved this run, in the shape the other stats use."""
        looked_up = self.hits + self.misses
        saved = (self.hits * 100.0 / looked_up) if looked_up else 0.0
        expired = f", {self.stale} expired or changed" if self.stale else ""
        return f"{self.hits} mirrored, {self.misses} fetched{expired} ({saved:.0f}% of requests saved)"


def _fresh(fetched_at: str, stored: str | None, fingerprint: str | None, max_age: timedelta | None) -> bool:
    """Whether a stored row still answers a lookup (see :meth:`PageCache.get`)."""
    if fingerprint is not None and stored != fingerprint:
        return False
    if max_age is None:
        return True
    stamp = datetime.fromisoformat(fetched_at)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return datetime.now(UTC) - stamp < max_age


def open_page_cache(path: str | Path | None = None, *, enabled: bool | None = None) -> PageCache | None:
    """The page mirror, or None when it is switched off.

    Both arguments default to the corresponding setting, read at call time so
    the environment can be set after this module is imported.
    """
    from composer_config import settings

    if enabled is None:
        enabled = settings.page_cache_enabled
    if not enabled:
        log.info("page cache disabled; every page will be fetched")
        return None
    cache = PageCache(Path(path if path is not None else settings.page_cache_path))
    log.info("mirroring fetched pages in %s", cache.path)
    return cache
