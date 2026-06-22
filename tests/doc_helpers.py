"""Test helpers: thin views over a generic ``Document`` so the per-source
parsing tests can keep asserting on ``.name`` / ``.claims`` / ``.raw`` /
``.title`` etc. instead of digging into ``doc.body``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from composer_ingest.document import Document, SourceClaim


def claims_of(doc: Document) -> tuple[SourceClaim, ...]:
    return tuple(SourceClaim(**claim) for claim in doc.body.get("claims", []))


@dataclass
class RecordView:
    """An entity document seen the way the old ``SourceRecord`` looked."""

    doc: Document

    @property
    def external_id(self) -> str:
        return self.doc.id

    @property
    def url(self) -> str | None:
        return self.doc.url

    @property
    def name(self) -> Any:
        return self.doc.body["name"]

    @property
    def kind(self) -> Any:
        return self.doc.body["kind"]

    @property
    def raw(self) -> Any:
        return self.doc.body["raw"]

    @property
    def claims(self) -> tuple[SourceClaim, ...]:
        return claims_of(self.doc)


@dataclass
class MentionView:
    """A work-mention document seen the way the old ``SourceWorkMention`` looked."""

    doc: Document

    @property
    def external_id(self) -> str:
        return self.doc.id

    @property
    def title(self) -> Any:
        return self.doc.body["title"]

    @property
    def composer(self) -> Any:
        return self.doc.body["composer"]

    @property
    def raw(self) -> Any:
        return self.doc.body["raw"]
