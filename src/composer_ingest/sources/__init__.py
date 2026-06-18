"""Data sources. Each source module exposes NAME, BASE_URL, and
``fetch_records(max_pages=None) -> Iterator[SourceRecord]``. Register new
sources in REGISTRY to make them available to the CLI."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol


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


@dataclass(frozen=True)
class SourceRecord:
    external_id: str
    name: str
    url: str | None
    raw: dict[str, Any]
    # what the record describes: "person", "work", ... (see models.Entity)
    kind: str = "person"
    # claims this source makes about the entity; empty when the source doesn't
    # say anything beyond the name (IMSLP's people list doesn't distinguish
    # composers from performers, editors, ensembles)
    claims: tuple[SourceClaim, ...] = ()


@dataclass(frozen=True)
class SourceWorkMention:
    """A (composer, title) pair as a source reported it — e.g. one work on a
    concert programme. The ingest resolves it to a canonical work (match, review
    or create). ``raw`` keeps the full performance context (date, conductor,
    soloists, venue) so a later pass can build performance events without
    re-fetching."""

    external_id: str
    title: str
    composer: str | None
    raw: dict[str, Any]


class SourceLike(Protocol):
    """What the ingest pipeline needs from a source: the modules in this
    package satisfy it, and tests can substitute fakes."""

    NAME: str
    BASE_URL: str

    def fetch_records(self, max_pages: int | None = None) -> Iterator[SourceRecord | SourceWorkMention]: ...


from . import berlinphil, concertgebouw, imslp, nyphil, wikidata  # noqa: E402

REGISTRY: dict[str, SourceLike] = {
    imslp.NAME: imslp,
    wikidata.NAME: wikidata,
    concertgebouw.NAME: concertgebouw,
    nyphil.NAME: nyphil,
    berlinphil.NAME: berlinphil,
}
