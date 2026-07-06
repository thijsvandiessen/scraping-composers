"""FastAPI applications for composer data (gold = curated, bronze = raw)."""

from .main import app, bronze_app, create_app, gold_app

__all__ = ["app", "bronze_app", "create_app", "gold_app"]
