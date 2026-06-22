"""The uniform document every source produces.

A source used to emit two different shapes (an entity record with claims, and a
work mention); both are now one :class:`Document`. Every document carries the
same base fields — a source-scoped ``id``, a ``url``, an ``ingested_at``
timestamp and the ``source_name`` — plus a ``doc_type`` discriminator and a
freeform ``body`` whose shape depends on ``doc_type``:

- ``doc_type="entity"``: ``body = {"name", "kind", "claims": [...], "raw": {...}}``
- ``doc_type="work_mention"``: ``body = {"title", "composer", "raw": {...}}``

``content_hash`` is a digest of the canonicalized ``body`` (not the volatile
base fields), so a re-fetch can tell "seen again, unchanged" from "content
changed". Parsers build documents with the :func:`entity_document` /
:func:`work_mention_document` factories and leave ``source_name`` /
``ingested_at`` / ``content_hash`` empty; :func:`stamp` (called centrally by the
scraper) fills them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class SourceClaim:
    """An assertion the source makes about an entity document.

    The object is either another entity (set ``object_kind`` + ``object_label``,
    e.g. ("profession", "composer") for has_profession) or a literal (set
    ``value``, e.g. "1756-01-27" for born_on).
    """

    predicate: str
    object_kind: str | None = None
    object_label: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class Document:
    """A single piece of information found by a source.

    ``source_name`` / ``ingested_at`` / ``content_hash`` are stamped by
    :func:`stamp`; parsers leave them empty.
    """

    id: str  # source-scoped stable id (was external_id)
    source_name: str
    url: str | None
    ingested_at: str  # ISO-8601 UTC, stamped at fetch time
    doc_type: str  # discriminator: "entity" | "work_mention"
    content_hash: str  # sha256 of the canonical body; change-detection key
    body: dict[str, Any]  # freeform; shape depends on doc_type


def content_hash(body: dict[str, Any]) -> str:
    """A stable sha256 of ``body`` (key order does not matter), used to detect
    when a document's content changed between fetches."""
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp(doc: Document, source_name: str) -> Document:
    """Fill the centrally-managed fields: the source name, the ingestion time
    (kept if already set) and the content hash. Idempotent in ``content_hash``:
    the same body always hashes the same."""
    return dataclasses.replace(
        doc,
        source_name=source_name,
        ingested_at=doc.ingested_at or _utc_now_iso(),
        content_hash=content_hash(doc.body),
    )


def entity_document(
    id: str,
    name: str,
    url: str | None = None,
    kind: str = "person",
    claims: tuple[SourceClaim, ...] = (),
    raw: dict[str, Any] | None = None,
) -> Document:
    """A document describing an entity (person, profession, place, work, ...)
    and the claims a source makes about it."""
    body: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "claims": [dataclasses.asdict(claim) for claim in claims],
        "raw": raw if raw is not None else {},
    }
    return Document(
        id=id,
        source_name="",
        url=url,
        ingested_at="",
        doc_type="entity",
        content_hash="",
        body=body,
    )


def work_mention_document(
    id: str,
    title: str,
    composer: str | None,
    raw: dict[str, Any] | None = None,
) -> Document:
    """A document for a (composer, title) pair as a source reported it — e.g.
    one work on a concert programme. ``raw`` keeps the full performance context
    (date, conductor, soloists, venue) for a later performances pass."""
    body: dict[str, Any] = {
        "title": title,
        "composer": composer,
        "raw": raw if raw is not None else {},
    }
    return Document(
        id=id,
        source_name="",
        url=None,
        ingested_at="",
        doc_type="work_mention",
        content_hash="",
        body=body,
    )
