"""Dependency-free test factories for source documents and a fake adapter.

These build :class:`~composer_schema.EntityDocument` /
:class:`~composer_schema.WorkMentionDocument` values and a stand-in
:class:`~composer_schema.SourceAdapter`, so any tier can construct source
documents in tests without importing the scraper or warehouse stacks. The
warehouse's ``testing`` plugin re-exports these alongside its DB fixtures.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from composer_schema import (
    EntityDocument,
    SourceAdapter,
    SourceClaim,
    WorkMentionDocument,
)

_INGESTED_AT = datetime(2024, 1, 1, tzinfo=UTC)


class FakeSource(SourceAdapter):
    """In-memory stand-in for a source adapter (satisfies SourceAdapter)."""

    name = "fake"
    base_url = "https://fake.example"

    def __init__(
        self,
        records: tuple[EntityDocument | WorkMentionDocument, ...],
        name: str = "fake",
        base_url: str = "https://fake.example",
        fail_after: int | None = None,
    ) -> None:
        self._records = records
        self.name = name  # pyright: ignore[reportAttributeAccessIssue]
        self.base_url = base_url  # pyright: ignore[reportAttributeAccessIssue]
        self.fail_after = fail_after

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        for i, record in enumerate(self._records):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("source exploded")
            yield record


def person(
    name: str, *claims: SourceClaim, external_id: str | None = None, url: str | None = None
) -> EntityDocument:
    """A person document. Pass a ``wikidata.org/wiki/Q...`` ``url`` to give the
    entity a QID, which ingest folds into its dedup key — the authority id the
    dedupe pass reads as "this is a specific wikidata item"."""
    return EntityDocument(
        id=external_id or f"Category:{name}",
        url=url,
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name=name,
        raw={"id": name},
        claims=claims,
    )


def ensemble(
    name: str, *claims: SourceClaim, external_id: str | None = None, url: str | None = None
) -> EntityDocument:
    """A group (orchestra, choir) — the ``ensemble`` entity kind. A
    ``wikidata.org/wiki/Q...`` ``url`` keys the ensemble on its QID."""
    return EntityDocument(
        id=external_id or f"Category:{name}",
        url=url,
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name=name,
        kind="ensemble",
        raw={"id": name},
        claims=claims,
    )


def genre(
    qid: str,
    label: str,
    *parents: tuple[str, str],
    kind: str = "genre",
    aliases: tuple[str, ...] = (),
) -> EntityDocument:
    """A genre (or, with ``kind="movement"``, a movement) document as the
    ``wikidata_genres`` source emits it: pinned to ``qid`` by its URL, with a
    ``subclass_of`` claim per ``(parent QID, parent label)``."""
    claims = [SourceClaim("subclass_of", kind, plabel, object_wikidata_id=pqid) for pqid, plabel in parents]
    claims += [SourceClaim("also_known_as", value=alias) for alias in aliases]
    return EntityDocument(
        id=f"{kind}:{qid}",
        url=f"https://www.wikidata.org/wiki/{qid}",
        source_name="wikidata_genres",
        ingested_at=_INGESTED_AT,
        name=label,
        kind=kind,
        raw={"item": qid},
        claims=tuple(claims),
    )


def mention(title: str, composer: str | None, external_id: str = "m1") -> WorkMentionDocument:
    return WorkMentionDocument(
        id=external_id,
        url=None,
        source_name="fake",
        ingested_at=_INGESTED_AT,
        title=title,
        composer=composer,
        raw={"title": title},
    )


def perf_mention(external_id: str, title: str, composer: str, raw: dict[str, Any]) -> WorkMentionDocument:
    """A work mention with a realistic performance-context payload."""
    return WorkMentionDocument(
        id=external_id,
        url=None,
        source_name="fake",
        ingested_at=_INGESTED_AT,
        title=title,
        composer=composer,
        raw=raw,
    )
