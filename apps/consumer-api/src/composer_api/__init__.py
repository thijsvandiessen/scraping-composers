"""FastAPI applications for composer data (gold = curated, silver = staging)."""

from .main import app, bronze_app, create_app, gold_app, silver_app

__all__ = ["app", "bronze_app", "create_app", "gold_app", "silver_app"]
