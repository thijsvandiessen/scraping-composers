"""The two consumer API apps.

``gold_app`` (the default product API) serves the curated gold database built
by ``composer-ingest promote``; ``bronze_app`` serves the raw staging
database. Same routes, different databases:

    uv run uvicorn composer_api:gold_app --port 8000
    uv run uvicorn composer_api:bronze_app --port 8003
"""

from collections.abc import Callable, Generator

from composer_gold import DEFAULT_GOLD_DB_PATH
from composer_warehouse.db import get_engine, init_db
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .deps import get_db
from .routes import v1


def create_app(title: str, factory_provider: Callable[[], sessionmaker[Session]]) -> FastAPI:
    """A consumer API app bound to its own database.

    ``factory_provider`` is called lazily on the first request, so importing
    this module never touches (or creates) a database file.
    """
    app = FastAPI(title=title)
    app.include_router(v1)
    cached: sessionmaker[Session] | None = None

    def _get_db() -> Generator[Session, None, None]:
        nonlocal cached
        if cached is None:
            cached = factory_provider()
        with cached() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    return app


def _gold_factory() -> sessionmaker[Session]:
    # NullPool: every request opens the file fresh, so the atomic swap done by
    # `promote` (os.replace) is picked up without restarting the app.
    engine = create_engine(f"sqlite:///{DEFAULT_GOLD_DB_PATH}", poolclass=NullPool)
    return init_db(engine)


def _bronze_factory() -> sessionmaker[Session]:
    return init_db(get_engine())


gold_app = create_app("Composer API (gold — curated)", _gold_factory)
bronze_app = create_app("Composer API (bronze — raw staging)", _bronze_factory)

# The unqualified app is the product API: gold.
app = gold_app
