"""Data sources. Each source exposes a SourceAdapter subclass that implements
``fetch(max_pages=None) -> Iterator[EntityDocument | WorkMentionDocument]``.
Register new sources in REGISTRY to make them available to the CLI.

The document/adapter contracts live in :mod:`composer_schema` and are re-exported
here so each source can keep importing them from its parent package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from composer_schema import (
    EntityDocument,
    RefreshCadence,
    ScrapedDocument,
    SourceAdapter,
    SourceClaim,
    WorkMentionDocument,
    is_due,
)

__all__ = [
    "REGISTRY",
    "EntityDocument",
    "RefreshCadence",
    "ScrapedDocument",
    "SourceAdapter",
    "SourceClaim",
    "SourceRecord",
    "SourceWorkMention",
    "WorkMentionDocument",
    "is_due",
]


# ---------------------------------------------------------------------------
# Internal parse types — used by source-specific parse functions only.
# Public adapter output uses EntityDocument / WorkMentionDocument (composer_schema).
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


from ._pdf import PdfSourceAdapter as PdfSourceAdapter  # noqa: E402
from .berlinphil import BerlinPhilAdapter  # noqa: E402
from .classicalcomposersposter import ClassicalComposersPosterAdapter  # noqa: E402
from .concertgebouw import ConcertgebouwAdapter  # noqa: E402
from .imslp import ImslpAdapter  # noqa: E402
from .nyphil import NyPhilAdapter  # noqa: E402
from .openopus import OpenOpusAdapter  # noqa: E402
from .rco import RcoAdapter  # noqa: E402
from .wikidata import WikidataAdapter  # noqa: E402

REGISTRY: dict[str, SourceAdapter] = {
    "imslp": ImslpAdapter(),
    "wikidata": WikidataAdapter(),
    "concertgebouw_archive": ConcertgebouwAdapter(),
    "nyphil": NyPhilAdapter(),
    "berlinphil": BerlinPhilAdapter(),
    "classicalcomposersposter": ClassicalComposersPosterAdapter(),
    "rco": RcoAdapter(),
    "openopus": OpenOpusAdapter(),
}
