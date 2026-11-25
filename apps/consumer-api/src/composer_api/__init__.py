"""FastAPI applications for composer data (gold = curated, silver = staging)."""

from .main import app, create_app, gold_app, silver_app

__all__ = ["app", "create_app", "gold_app", "silver_app"]
