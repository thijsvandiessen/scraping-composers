"""Shared pytest fixtures and helpers for the warehouse and downstream tiers.

Members re-export the ``session`` fixture from this module in their
``tests/conftest.py`` (a plain import, since ``pytest_plugins`` is only legal
in the rootdir conftest); test modules import the factories below directly. The document/adapter factories
(``person``, ``ensemble``, ``mention``, ``perf_mention``, ``FakeSource``) come from
:mod:`composer_schema.testing` and are re-exported here for convenience.
"""

from collections.abc import Iterator

import pytest
from composer_models import IngestRun
from composer_models.db import get_engine, init_db
from composer_schema import SourceAdapter
from composer_schema.testing import (
    FakeSource as FakeSource,
)
from composer_schema.testing import (
    ensemble as ensemble,
)
from composer_schema.testing import (
    mention as mention,
)
from composer_schema.testing import (
    perf_mention as perf_mention,
)
from composer_schema.testing import (
    person as person,
)
from sqlalchemy.orm import Session

from composer_warehouse.ingestion import ingest_documents


@pytest.fixture
def session() -> Iterator[Session]:
    """Session on a fresh in-memory SQLite database."""
    factory = init_db(get_engine("sqlite://"))
    with factory() as session:
        yield session


def ingest_source(session: Session, source: SourceAdapter) -> IngestRun:
    """Ingest a fake source's documents directly (test-side stand-in for fetch + process)."""
    return ingest_documents(session, source.name, source.base_url, source.fetch())
