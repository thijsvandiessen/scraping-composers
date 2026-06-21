from fastapi import FastAPI

from .routes import v1

app = FastAPI(title="Composer API")
app.include_router(v1)
