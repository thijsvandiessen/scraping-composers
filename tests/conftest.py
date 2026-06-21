from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from composer_ingest.etl.db import get_engine, init_db


@pytest.fixture
def session() -> Iterator[Session]:
    """Session on a fresh in-memory SQLite database."""
    factory = init_db(get_engine("sqlite://"))
    with factory() as session:
        yield session
