from fastapi import FastAPI

from .build_routes import builds
from .crawl_routes import crawls
from .logconfig import configure_logging
from .routes import admin

# Before the app exists: the background crawl/extract stages log their progress,
# and under uvicorn nothing would carry it to the console otherwise.
configure_logging()

admin_app = FastAPI(title="Composer Admin API")
admin_app.include_router(admin)
admin_app.include_router(builds)
admin_app.include_router(crawls)
