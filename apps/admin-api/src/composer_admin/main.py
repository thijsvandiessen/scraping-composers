from fastapi import FastAPI

from .build_routes import builds
from .logconfig import configure_logging
from .routes import admin

# Before the app exists: background fetches and builds log their progress, and
# under uvicorn nothing would carry it to the console otherwise.
configure_logging()

admin_app = FastAPI(title="Composer Admin API")
admin_app.include_router(admin)
admin_app.include_router(builds)
