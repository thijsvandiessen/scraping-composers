"""Data sources. Each source exposes a SourceAdapter subclass that implements
``fetch(max_pages=None) -> Iterator[EntityDocument | WorkMentionDocument]``.
Register new sources in REGISTRY to make them available to the CLI."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar


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
# Internal parse types — used by source-specific parse functions only.
# Public adapter output uses EntityDocument / WorkMentionDocument below.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRecord:
    external_id: str
    name: str
    url: str | None
    raw: dict[str, Any]
    kind: str = "person"
    claims: tuple[SourceClaim, ...] = ()


@dataclass(frozen=True)
class SourceWorkMention:
    """A (composer, title) pair as a source reported it — e.g. one work on a
    concert programme. The ingest resolves it to a canonical work (match,
    review or create). ``raw`` keeps the full performance context so a later
    pass can build performance events without re-fetching."""

    external_id: str
    title: str
    composer: str | None
    raw: dict[str, Any]


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


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class SourceAdapter(ABC):
    """Base class for source-specific scraping adapters.

    Subclasses define ``name`` and ``base_url`` as class-level constants and
    implement ``fetch``. The generic :class:`~composer_ingest.scraper.Scraper`
    drives the adapter and writes results to a bucket.
    """

    name: ClassVar[str]
    base_url: ClassVar[str]

    @abstractmethod
    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]: ...


from .berlinphil import BerlinPhilAdapter  # noqa: E402
from .classicalcomposersposter import ClassicalComposersPosterAdapter  # noqa: E402
from .concertgebouw import ConcertgebouwAdapter  # noqa: E402
from .imslp import ImslpAdapter  # noqa: E402
from .nyphil import NyPhilAdapter  # noqa: E402
from .wikidata import WikidataAdapter  # noqa: E402

REGISTRY: dict[str, SourceAdapter] = {
    "imslp": ImslpAdapter(),
    "wikidata": WikidataAdapter(),
    "concertgebouw": ConcertgebouwAdapter(),
    "nyphil": NyPhilAdapter(),
    "berlinphil": BerlinPhilAdapter(),
    "classicalcomposersposter": ClassicalComposersPosterAdapter(),
}
