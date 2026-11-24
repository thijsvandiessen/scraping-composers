"""Internal parse types used by source-specific parse functions only.

Public adapter output uses :class:`~composer_schema.EntityDocument` and
:class:`~composer_schema.WorkMentionDocument`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from composer_schema import SourceClaim

__all__ = ["SourceRecord", "SourceWorkMention"]


@dataclass(frozen=True)
class SourceRecord:
    external_id: str
    name: str
    url: str | None
    raw: dict[str, Any]
    kind: str = "person"
    claims: tuple[SourceClaim, ...] = ()
    #: the language the source states for ``name`` (see ``ScrapedDocument``)
    language: str | None = None


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
