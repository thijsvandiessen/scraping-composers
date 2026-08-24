"""Source contracts shared across the scraping tiers.

The document types (:class:`EntityDocument`, :class:`WorkMentionDocument`) are
the seam between the scrapers (which produce them) and the warehouse (which
ingests them); :class:`SourceAdapter` is the interface every scraper implements.
Kept dependency-free so every tier can depend on it without pulling in httpx,
sqlalchemy, or the scraper stack.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar

from .kinds import (
    ENSEMBLE_KIND as ENSEMBLE_KIND,
)
from .kinds import (
    PERSON_KIND as PERSON_KIND,
)
from .kinds import (
    looks_like_ensemble as looks_like_ensemble,
)
from .kinds import (
    resolve_entity_kind as resolve_entity_kind,
)


@dataclass(frozen=True)
class SourceClaim:
    """An assertion the source makes about the record's entity.

    The object is either another entity (set ``object_kind`` +
    ``object_label``, e.g. ("profession", "composer") for has_profession) or
    a literal (set ``value``, e.g. "1756-01-27" for born_on).
    """

    predicate: str
    object_kind: str | None = None
    object_label: str | None = None
    value: str | None = None


# ---------------------------------------------------------------------------
# Public document types — the normalised output of every SourceAdapter.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScrapedDocument:
    """Envelope common to every document produced by a source adapter."""

    id: str
    url: str | None
    source_name: str
    ingested_at: datetime


@dataclass(frozen=True)
class EntityDocument(ScrapedDocument):
    """A person, work, place, or other named entity from a source."""

    name: str = ""
    kind: str = "person"
    raw: dict[str, Any] = field(default_factory=dict)
    claims: tuple[SourceClaim, ...] = ()


@dataclass(frozen=True)
class WorkMentionDocument(ScrapedDocument):
    """A (composer, title) pair from a concert programme or similar source."""

    title: str = ""
    composer: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def serialize_document(doc: EntityDocument | WorkMentionDocument) -> dict[str, Any]:
    """A JSON-safe dict for *doc*, round-tripped by :func:`deserialize_document`.

    Shared by the bucket (a snapshot's NDJSON lines) and the extract ledger (a
    page's carried-forward documents), so both use one implementation of
    "how a document survives disk" rather than two that can drift apart.
    """
    # asdict recursively converts nested dataclasses (including SourceClaim) to dicts
    d = asdict(doc)
    # datetime is not JSON-serialisable; replace with ISO 8601 string
    d["ingested_at"] = doc.ingested_at.isoformat()
    d["_type"] = "entity" if isinstance(doc, EntityDocument) else "work_mention"
    return d


def deserialize_document(d: dict[str, Any]) -> EntityDocument | WorkMentionDocument:
    """The document *d* (from :func:`serialize_document`) was serialized from."""
    d = dict(d)
    kind = d.pop("_type")
    d["ingested_at"] = datetime.fromisoformat(d["ingested_at"])
    if kind == "entity":
        claims = tuple(SourceClaim(**c) for c in d.pop("claims", []))
        return EntityDocument(**d, claims=claims)
    if kind == "work_mention":
        return WorkMentionDocument(**d)
    raise ValueError(f"unknown _type in document payload: {kind!r}")


# ---------------------------------------------------------------------------
# Refresh cadence — how often a source's data is worth re-scraping. Drives the
# admin interface's "what's due" view so scrapes can be triggered by staleness.
# ---------------------------------------------------------------------------


class RefreshCadence(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    STATIC = "static"  # rarely/never changes; only run on demand, never auto-due

    @property
    def interval(self) -> timedelta | None:
        """How long fetched data stays fresh, or ``None`` if it never goes stale."""
        return {
            RefreshCadence.WEEKLY: timedelta(days=7),
            RefreshCadence.MONTHLY: timedelta(days=30),
            RefreshCadence.YEARLY: timedelta(days=365),
            RefreshCadence.STATIC: None,
        }[self]


def is_due(cadence: RefreshCadence, last_started_at: datetime | None, now: datetime) -> bool:
    """Whether a source is due for a refresh given its cadence and last run.

    STATIC sources are never automatically due; a source that has never run is
    always due; otherwise it is due once its cadence interval has elapsed.
    """
    interval = cadence.interval
    if interval is None:
        return False
    if last_started_at is None:
        return True
    # SQLite returns stored UTC timestamps as naive; treat them as UTC so the
    # comparison works regardless of backend.
    if last_started_at.tzinfo is None:
        last_started_at = last_started_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now - last_started_at >= interval


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class SourceAdapter(ABC):
    """Base class for source-specific scraping adapters.

    Subclasses define ``name`` and ``base_url`` as class-level constants and
    implement ``fetch``. The generic :class:`composer_bronze.Scraper` drives the
    adapter and writes results to a bucket. ``cadence`` declares how often the
    source is worth re-scraping (see :class:`RefreshCadence`).
    """

    name: ClassVar[str]
    base_url: ClassVar[str]
    cadence: ClassVar[RefreshCadence] = RefreshCadence.MONTHLY

    @abstractmethod
    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]: ...
