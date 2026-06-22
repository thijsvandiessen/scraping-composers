"""Data sources. Each source module builds a :class:`~composer_ingest.scraper.Scraper`
(config + injected ``pages``/``parse``) exposed as ``SCRAPER`` and yielding
uniform :class:`~composer_ingest.document.Document` objects. Register new
sources in REGISTRY to make them available to the CLI."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from ..document import (
    Document,
    SourceClaim,
    content_hash,
    entity_document,
    stamp,
    work_mention_document,
)
from ..scraper import Scraper, SourceConfig

__all__ = [
    "REGISTRY",
    "Document",
    "Scraper",
    "SourceClaim",
    "SourceConfig",
    "SourceLike",
    "content_hash",
    "entity_document",
    "stamp",
    "work_mention_document",
]


class SourceLike(Protocol):
    """What the ingest pipeline needs from a source: the ``Scraper`` instances
    in this package satisfy it, and tests can substitute fakes."""

    @property
    def NAME(self) -> str: ...

    @property
    def BASE_URL(self) -> str: ...

    def fetch_documents(self, max_pages: int | None = None) -> Iterator[Document]: ...


from . import berlinphil, concertgebouw, imslp, nyphil, wikidata  # noqa: E402

REGISTRY: dict[str, Scraper[Any]] = {
    imslp.NAME: imslp.SCRAPER,
    wikidata.NAME: wikidata.SCRAPER,
    concertgebouw.NAME: concertgebouw.SCRAPER,
    nyphil.NAME: nyphil.SCRAPER,
    berlinphil.NAME: berlinphil.SCRAPER,
}
