from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from .routes import admin

admin_app = FastAPI(title="Composer Admin API")
admin_app.include_router(admin)
