"""The two consumer API apps.

``gold_app`` (the default product API) serves the curated gold database built
by ``composer-ingest promote``; ``silver_app`` serves the silver staging
database. Same routes, different databases:

    uv run uvicorn composer_api:gold_app --port 8000
    uv run uvicorn composer_api:silver_app --port 8003
"""

from collections.abc import Callable, Generator

from composer_config import settings
from composer_gold import gold_engine
from composer_models.db import get_engine, init_db
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import ServiceUnavailable
from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .deps import get_db, get_graph
from .errors import GraphUnavailableError, NotFoundError
from .routes import v1


def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    # Typed (Request, Exception) because Starlette's add_exception_handler
    # expects Callable[[Request, Exception], Response]; the registration
    # below guarantees exc is a NotFoundError.
    assert isinstance(exc, NotFoundError)
    return JSONResponse(status_code=404, content={"detail": exc.detail})


def _graph_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    # Both the app having no graph and the graph not answering: the gold
    # endpoints keep working, only the connections are missing.
    detail = exc.detail if isinstance(exc, GraphUnavailableError) else "the graph database is not reachable"
    return JSONResponse(status_code=503, content={"detail": detail})


def create_app(
    title: str,
    factory_provider: Callable[[], sessionmaker[Session]],
    graph_provider: Callable[[], Driver] | None = None,
) -> FastAPI:
    """A consumer API app bound to its own database, and optionally a graph.

    Both providers are called lazily on the first request that needs them, so
    importing this module never touches (or creates) a database file, and the
    app starts with Neo4j down.
    """
    app = FastAPI(title=title)
    app.include_router(v1)
    app.add_exception_handler(NotFoundError, _not_found_handler)
    app.add_exception_handler(GraphUnavailableError, _graph_unavailable_handler)
    app.add_exception_handler(ServiceUnavailable, _graph_unavailable_handler)
    cached: sessionmaker[Session] | None = None
    graph: Driver | None = None

    def _get_db() -> Generator[Session, None, None]:
        nonlocal cached
        if cached is None:
            cached = factory_provider()
        with cached() as session:
            yield session

    def _get_graph() -> Driver | None:
        nonlocal graph
        if graph is None and graph_provider is not None:
            graph = graph_provider()
        return graph

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_graph] = _get_graph
    return app


def _gold_factory() -> sessionmaker[Session]:
    # GOLD_DATABASE_URL, SQLite file or Postgres; gold_engine picks the pooling
    # that lets every request see a new promote without restarting the app.
    return init_db(gold_engine())


def _gold_graph() -> Driver:
    # The projection of the same gold (composer-ingest export-neo4j); creating
    # the driver opens no connection, so a missing Neo4j shows up per request.
    return GraphDatabase.driver(settings.neo4j_url, auth=(settings.neo4j_user, settings.neo4j_password))


def _silver_factory() -> sessionmaker[Session]:
    # NullPool for SQLite only: there the swap replaces the file, so a pooled
    # connection still points at the old inode. A Postgres rebuild renames
    # schemas instead, and name resolution happens per statement, so pooled
    # connections follow the swap on their own.
    if make_url(settings.database_url).drivername.partition("+")[0] == "sqlite":
        return init_db(create_engine(settings.database_url, poolclass=NullPool))
    return init_db(get_engine())


gold_app = create_app("Composer API (gold — curated)", _gold_factory, _gold_graph)
silver_app = create_app("Composer API (silver — staging)", _silver_factory)

# Deprecated alias: deployments may still reference composer_api:bronze_app.
bronze_app = silver_app

# The unqualified app is the product API: gold.
app = gold_app
