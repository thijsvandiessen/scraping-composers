"""Shared pytest fixtures and helpers for the composer workspace.

Members load this as a pytest plugin via ``pytest_plugins = ["composer_ingest.testing"]``
in their ``tests/conftest.py``; test modules import the factories below directly.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from composer_ingest.etl.db import get_engine, init_db
from composer_ingest.etl.ingestion import ingest_documents
from composer_ingest.etl.models import IngestRun
from composer_ingest.scraper.sources import (
    EntityDocument,
    SourceAdapter,
    SourceClaim,
    WorkMentionDocument,
)

_INGESTED_AT = datetime(2024, 1, 1, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    """Session on a fresh in-memory SQLite database."""
    factory = init_db(get_engine("sqlite://"))
    with factory() as session:
        yield session


def ingest_source(session: Session, source: SourceAdapter) -> IngestRun:
    """Ingest a fake source's documents directly (test-side stand-in for fetch + process)."""
    return ingest_documents(session, source.name, source.base_url, source.fetch())


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
        self.name = name  # type: ignore[misc]
        self.base_url = base_url  # type: ignore[misc]
        self.fail_after = fail_after

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        for i, record in enumerate(self._records):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("source exploded")
            yield record


def person(name: str, *claims: SourceClaim, external_id: str | None = None) -> EntityDocument:
    return EntityDocument(
        id=external_id or f"Category:{name}",
        url=None,
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name=name,
        raw={"id": name},
        claims=claims,
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
