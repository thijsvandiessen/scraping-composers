"""Remember pages a scraper already fetched, so an archive is fetched once.

Most sources here are a dozen requests; a few are one request *per record*. The
Vienna Philharmonic concert archive is the extreme: 10,749 detail pages, one per
concert, which at a polite request rate is a three-hour sweep. That sweep is
worth paying once and never again — the archive is a historical record, so a
page fetched today says the same thing next month.

So this is a mirror, not a cache in the expiring sense: there is no TTL, and a
stored page is served forever. Deleting the file is the hard reset, and
``PAGE_CACHE_ENABLED=false`` bypasses it for a run.

Bodies are gzipped, which matters at this scale: the Vienna archive's pages are
~36KB each and compress to ~10KB, so the whole mirror is ~100MB rather than
~380MB.

Modelled on :mod:`composer_extract.cache`, including its failure policy: a cache
is an optimization and never a reason to fail, so every SQLite error degrades to
"not cached" and is logged.
"""

from __future__ import annotations

import gzip
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS page_cache (
    url        TEXT PRIMARY KEY,
    body       BLOB NOT NULL,
    fetched_at TEXT NOT NULL
)
"""


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
    _ready: bool = field(default=False, repr=False)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        if not self._ready:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(_SCHEMA)
            connection.commit()
            self._ready = True
        return connection

    def get(self, url: str) -> str | None:
        """The stored body for *url*, or None when it has not been fetched."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT body FROM page_cache WHERE url = ?", (url,)).fetchone()
            body = gzip.decompress(row[0]).decode("utf-8") if row is not None else None
        except (sqlite3.Error, OSError, EOFError, UnicodeDecodeError) as exc:
            # a corrupt row is as good as a missing one: refetch beats crashing
            log.warning("page cache: lookup failed (%s: %s); treating as a miss", type(exc).__name__, exc)
            return None
        if body is None:
            self.misses += 1
            return None
        self.hits += 1
        return body

    def put(self, url: str, body: str) -> None:
        """Store *body* for *url*; an existing row is replaced."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO page_cache (url, body, fetched_at) VALUES (?, ?, ?)",
                    (url, gzip.compress(body.encode("utf-8")), datetime.now(UTC).isoformat()),
                )
        except (sqlite3.Error, OSError) as exc:
            log.warning("page cache: write failed (%s: %s); continuing uncached", type(exc).__name__, exc)

    def summary(self) -> str:
        """What the mirror saved this run, in the shape the other stats use."""
        looked_up = self.hits + self.misses
        saved = (self.hits * 100.0 / looked_up) if looked_up else 0.0
        return f"{self.hits} mirrored, {self.misses} fetched ({saved:.0f}% of requests saved)"


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
