"""Remember what a page last produced, so an unchanged page skips the model
entirely — not just the network call the answer cache already saves.

Lives beside :mod:`.cache` (a second table in the same ``extract-cache.db``)
because it answers a related but different question: the cache asks "was this
exact chunk request already answered"; the ledger asks "was this whole page,
for this kind, already fully turned into documents, at this content hash and
this fingerprint of everything else that shapes the answer." A hit here skips
chunking, prompt building, and the cache lookup itself, not just the model
call — the gain is real because most of a large source does not change
between runs, and today every page is re-chunked and re-hashed once per
enabled extract kind regardless.

The match key is ``(content_sha256, extractor_fingerprint)`` rather than
content alone: a page whose text has not changed can still deserve a fresh
answer if the prompt, schema, model, or generation options have changed since
it was last extracted. The fingerprint reuses :func:`.cache.request_key` with
an empty page, so it invalidates on exactly what already invalidates the
answer cache — no version constant to remember to bump.

Only a page that fully succeeds is ledgered, mirroring how the answer cache
only stores validated responses: a page that errors is simply retried next
run rather than left half-recorded.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from composer_schema import EntityDocument, WorkMentionDocument, deserialize_document, serialize_document

from .cache import request_key

log = logging.getLogger(__name__)

Document = EntityDocument | WorkMentionDocument

_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_extract_state (
    source                 TEXT NOT NULL,
    final_url              TEXT NOT NULL,
    extract_kind           TEXT NOT NULL,
    content_sha256         TEXT NOT NULL,
    extractor_fingerprint  TEXT NOT NULL,
    extracted_at           TEXT NOT NULL,
    documents              TEXT NOT NULL,
    PRIMARY KEY (source, final_url, extract_kind)
)
"""


@dataclass(frozen=True)
class LedgerKey:
    """Identifies one page's entry: the source and page it belongs to, the kind
    it was extracted for, and what must still match for it to be reusable (the
    page's content and the extractor's fingerprint)."""

    source: str
    final_url: str
    kind: str
    content_sha256: str
    extractor_fingerprint: str


def request_fingerprint(extractor: Any, system_prompt: str, schema: type[Any]) -> str:
    """Fingerprint of everything that shapes *extractor*'s answer for *schema*,
    except the page content.

    *extractor* need only offer ``.model`` and ``.request_options()`` (matches
    :class:`~.client.OllamaExtractor`). Reuses :func:`request_key` with an
    empty user prompt so this invalidates together with the answer cache on
    the same changes (model, system prompt, schema, options) instead of
    duplicating that invalidation logic.
    """
    return request_key(
        model=extractor.model,
        system_prompt=system_prompt,
        user_prompt="",
        schema=schema.model_json_schema(),
        options=extractor.request_options(),
    )


@dataclass
class DocumentLedger:
    """SQLite-backed record of what each page last produced, per extract kind.

    Connections are opened per operation and closed again, same as
    :class:`~.cache.ExtractCache`: an extract may run in the admin API's
    background task while the CLI runs one of its own, and WAL mode lets those
    readers and writers coexist.
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

    def get(self, key: LedgerKey) -> list[Document] | None:
        """The page's documents from a previous run, or None to (re-)extract.

        A miss covers: no prior row, a changed hash or fingerprint, or a row
        whose stored documents no longer deserialize (the dataclass shape
        moved on since it was written) — the last case is logged and treated
        as a miss rather than raised, costing one re-extraction, not a crash.
        """
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT content_sha256, extractor_fingerprint, documents"
                    " FROM document_extract_state WHERE source = ? AND final_url = ? AND extract_kind = ?",
                    (key.source, key.final_url, key.kind),
                ).fetchone()
        except sqlite3.Error as exc:
            log.warning("extract ledger: lookup failed (%s: %s); treating as a miss", type(exc).__name__, exc)
            return None
        if row is None or row[0] != key.content_sha256 or row[1] != key.extractor_fingerprint:
            self.misses += 1
            return None
        try:
            documents = [deserialize_document(d) for d in json.loads(row[2])]
        except (ValueError, TypeError, KeyError) as exc:
            log.warning(
                "extract ledger: stored documents for %s (%s) no longer deserialize (%s: %s); re-extracting",
                key.final_url,
                key.kind,
                type(exc).__name__,
                exc,
            )
            self.misses += 1
            return None
        self.hits += 1
        return documents

    def put(self, key: LedgerKey, documents: list[Document]) -> None:
        """Record *documents* as the page's current output for *key*'s kind."""
        try:
            payload = json.dumps([serialize_document(d) for d in documents], ensure_ascii=False)
            row = (
                key.source,
                key.final_url,
                key.kind,
                key.content_sha256,
                key.extractor_fingerprint,
                datetime.now(UTC).isoformat(),
                payload,
            )
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO document_extract_state"
                    " (source, final_url, extract_kind, content_sha256, extractor_fingerprint,"
                    "  extracted_at, documents)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
        except sqlite3.Error as exc:
            log.warning(
                "extract ledger: write failed (%s: %s); continuing unledgered", type(exc).__name__, exc
            )

    def summary(self) -> str:
        """What the ledger saved this run, in the shape the other stats use."""
        looked_up = self.hits + self.misses
        saved = (self.hits * 100.0 / looked_up) if looked_up else 0.0
        return f"{self.hits} carried forward, {self.misses} extracted ({saved:.0f}% of pages skipped)"


def open_ledger(path: str | Path, *, enabled: bool = True) -> DocumentLedger | None:
    """The ledger at *path*, or None when it is switched off."""
    if not enabled:
        log.info("extract: ledger disabled; every page will be re-extracted")
        return None
    ledger = DocumentLedger(Path(path))
    log.info("extract: tracking per-page extraction state in %s", ledger.path)
    return ledger


@dataclass(frozen=True)
class LedgerContext:
    """What :func:`~.emit.emit_pages` needs to gate on the ledger: the store
    itself, plus the kind and extractor fingerprint held constant for every
    page of one extraction pass (only the page and its content hash vary)."""

    ledger: DocumentLedger
    kind: str
    extractor_fingerprint: str

    def key_for(self, *, source: str, final_url: str, content_sha256: str) -> LedgerKey:
        return LedgerKey(
            source=source,
            final_url=final_url,
            kind=self.kind,
            content_sha256=content_sha256,
            extractor_fingerprint=self.extractor_fingerprint,
        )
