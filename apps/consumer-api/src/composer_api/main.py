"""The two consumer API apps.

``gold_app`` (the default product API) serves the curated gold database built
by ``composer-ingest promote``; ``silver_app`` serves the silver staging
database. Same routes, different databases:

    uv run uvicorn composer_api:gold_app --port 8000
    uv run uvicorn composer_api:silver_app --port 8003
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from composer_gold import gold_engine
from composer_models.db import get_engine, init_db
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from .deps import DbSession
from .errors import GraphUnavailableError, NotFoundError
from .routes import v1


def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, NotFoundError):
        raise exc
    return JSONResponse(status_code=404, content={"detail": exc.detail})


def _graph_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    # The gold endpoints keep working, only the connections are missing.
    if not isinstance(exc, GraphUnavailableError):
        raise exc
    return JSONResponse(status_code=503, content={"detail": exc.detail})


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    try:
        yield
    finally:
        factory: sessionmaker[Session] | None = getattr(app.state, "session_factory", None)
        if factory is not None:
            bind = factory.kw.get("bind")
            if isinstance(bind, Engine):
                bind.dispose()
            app.state.session_factory = None


def create_app(title: str, factory_provider: Callable[[], sessionmaker[Session]]) -> FastAPI:
    """A consumer API app bound to its own database.

    The provider is called lazily on the first request, so importing this
    module never touches (or creates) a database file.
    """
    app = FastAPI(title=title, lifespan=_lifespan)
    app.state.factory_provider = factory_provider
    app.state.session_factory = None
    app.include_router(v1)
    app.add_exception_handler(NotFoundError, _not_found_handler)
    app.add_exception_handler(GraphUnavailableError, _graph_unavailable_handler)

    @app.get("/health")
    def health(db: DbSession) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


def _gold_factory() -> sessionmaker[Session]:
    # GOLD_DATABASE_URL, SQLite file or Postgres; gold_engine picks the pooling
    # that lets every request see a new promote without restarting the app.
    return init_db(gold_engine())


def _silver_factory() -> sessionmaker[Session]:
    return init_db(get_engine(follow_swap=True))


gold_app = create_app("Composer API (gold — curated)", _gold_factory)
silver_app = create_app("Composer API (silver — staging)", _silver_factory)

# Deprecated alias: deployments may still reference composer_api:bronze_app.
bronze_app = silver_app

# The unqualified app is the product API: gold.
app = gold_app
