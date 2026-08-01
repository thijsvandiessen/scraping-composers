"""Remember what the model already answered, so a page is analysed once.

A crawl writes a whole new snapshot every run, and most of a site does not change
between them. Without a cache, ``extract`` pays the full model cost again for text
it has already read — hours of GPU time on a large source.

The key is a fingerprint of the *exact request* rather than of the page, because
the answer is only reusable when every input that shaped it is identical: the
model, the system prompt, the user prompt (which folds in the page markdown *and*
its title/description metadata), the JSON schema demanded of the answer, and the
generation options. Editing :mod:`.prompt` therefore invalidates the cache by
itself — there is no version constant to remember to bump, which is the failure
mode that makes a prompt improvement look like it did nothing.

Only answers that validate are stored, so a run never caches the truncated JSON
that :mod:`.resilience` exists to survive. A cache is an optimization and never a
reason to fail: every SQLite error here degrades to "not cached" and is logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS extraction_cache (
    request_sha256 TEXT PRIMARY KEY,
    model          TEXT NOT NULL,
    schema_name    TEXT NOT NULL,
    response       TEXT NOT NULL,
    created_at     TEXT NOT NULL
)
"""


def request_key(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    options: dict[str, Any],
) -> str:
    """A stable SHA-256 over everything that shapes one model answer.

    Serialized with sorted keys so the digest does not depend on dict ordering.
    """
    payload = json.dumps(
        {
            "model": model,
            "system": system_prompt,
            "user": user_prompt,
            "schema": schema,
            "options": options,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ExtractCache:
    """SQLite-backed store of past model answers, keyed by :func:`request_key`.

    Connections are opened per operation and closed again: an extract may run in
    the admin API's background task while the CLI runs one of its own, and WAL
    mode lets those readers and writers coexist.
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

    def get(self, key: str) -> str | None:
        """The stored response for *key*, or None when it is not cached."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT response FROM extraction_cache WHERE request_sha256 = ?", (key,)
                ).fetchone()
        except sqlite3.Error as exc:
            log.warning("extract cache: lookup failed (%s: %s); treating as a miss", type(exc).__name__, exc)
            return None
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return str(row[0])

    def put(self, key: str, *, model: str, schema_name: str, response: str) -> None:
        """Store *response* for *key*; an existing row is replaced."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO extraction_cache"
                    " (request_sha256, model, schema_name, response, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (key, model, schema_name, response, datetime.now(UTC).isoformat()),
                )
        except sqlite3.Error as exc:
            log.warning("extract cache: write failed (%s: %s); continuing uncached", type(exc).__name__, exc)

    def delete(self, key: str) -> None:
        """Drop one entry — used when a stored answer no longer validates."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("DELETE FROM extraction_cache WHERE request_sha256 = ?", (key,))
        except sqlite3.Error as exc:
            log.warning("extract cache: delete failed (%s: %s)", type(exc).__name__, exc)

    def summary(self) -> str:
        """What the cache saved this run, in the shape the other stats use."""
        looked_up = self.hits + self.misses
        saved = (self.hits * 100.0 / looked_up) if looked_up else 0.0
        return f"{self.hits} cached, {self.misses} asked ({saved:.0f}% of calls saved)"


def open_cache(path: str | Path, *, enabled: bool = True) -> ExtractCache | None:
    """The cache at *path*, or None when caching is switched off."""
    if not enabled:
        log.info("extract: cache disabled; every page will be sent to the model")
        return None
    cache = ExtractCache(Path(path))
    log.info("extract: caching model answers in %s", cache.path)
    return cache
