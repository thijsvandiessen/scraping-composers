from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from composer_ingest.etl.db import get_engine, init_db
from composer_ingest.etl.ingestion import ingest_documents
from composer_ingest.etl.models import IngestRun
from composer_ingest.scraper.sources import SourceAdapter


@pytest.fixture
def session() -> Iterator[Session]:
    """Session on a fresh in-memory SQLite database."""
    factory = init_db(get_engine("sqlite://"))
    with factory() as session:
        yield session


def ingest_source(session: Session, source: SourceAdapter) -> IngestRun:
    """Ingest a fake source's documents directly (test-side stand-in for fetch + process)."""
    return ingest_documents(session, source.name, source.base_url, source.fetch())
