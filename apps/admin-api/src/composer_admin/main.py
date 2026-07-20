from fastapi import FastAPI

from .crawl_routes import crawls
from .routes import admin

admin_app = FastAPI(title="Composer Admin API")
admin_app.include_router(admin)
admin_app.include_router(crawls)
