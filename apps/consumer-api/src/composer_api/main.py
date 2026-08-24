"""The two consumer API apps.

``gold_app`` (the default product API) serves the curated gold database built
by ``composer-ingest promote``; ``silver_app`` serves the silver staging
database. Same routes, different databases:

    uv run uvicorn composer_api:gold_app --port 8000
    uv run uvicorn composer_api:silver_app --port 8003
"""

from collections.abc import Callable, Generator

from composer_config import settings
from composer_gold import DEFAULT_GOLD_DB_PATH
from composer_models.db import get_engine, init_db
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .deps import get_db
from .errors import NotFoundError
from .routes import v1


def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    # Typed (Request, Exception) because Starlette's add_exception_handler
    # expects Callable[[Request, Exception], Response]; the registration
    # below guarantees exc is a NotFoundError.
    assert isinstance(exc, NotFoundError)
    return JSONResponse(status_code=404, content={"detail": exc.detail})


def create_app(title: str, factory_provider: Callable[[], sessionmaker[Session]]) -> FastAPI:
    """A consumer API app bound to its own database.

    ``factory_provider`` is called lazily on the first request, so importing
    this module never touches (or creates) a database file.
    """
    app = FastAPI(title=title)
    app.include_router(v1)
    app.add_exception_handler(NotFoundError, _not_found_handler)
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


def _silver_factory() -> sessionmaker[Session]:
    # For a sqlite file, NullPool for the same reason as gold: `rebuild-silver`
    # swaps the file with os.replace, and pooled connections would keep serving
    # the old inode until a restart.
    # NullPool for SQLite only: there the swap replaces the file, so a pooled
    # connection still points at the old inode. A Postgres rebuild renames
    # schemas instead, and name resolution happens per statement, so pooled
    # connections follow the swap on their own.
    if make_url(settings.database_url).drivername.partition("+")[0] == "sqlite":
        return init_db(create_engine(settings.database_url, poolclass=NullPool))
    return init_db(get_engine())


gold_app = create_app("Composer API (gold — curated)", _gold_factory)
silver_app = create_app("Composer API (silver — staging)", _silver_factory)

# Deprecated alias: deployments may still reference composer_api:bronze_app.
bronze_app = silver_app

# The unqualified app is the product API: gold.
app = gold_app
