from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .build_routes import builds
from .deps import dispose_db
from .logconfig import configure_logging
from .routes import admin


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # Background fetches and builds log their progress; under uvicorn nothing
    # would carry it to the console without explicit handler configuration.
    configure_logging()
    try:
        yield
    finally:
        dispose_db()


admin_app = FastAPI(title="Composer Admin API", lifespan=_lifespan)
admin_app.include_router(admin)
admin_app.include_router(builds)


@admin_app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
