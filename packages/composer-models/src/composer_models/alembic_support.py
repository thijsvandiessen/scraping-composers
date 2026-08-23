"""Programmatic access to the migration tree.

Alembic is an optional dependency (the ``postgres`` extra), so every import of
it is function-local: a SQLite-only install never needs it, because that schema
comes from ``create_all`` and is rebuilt from bronze rather than migrated.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from composer_config import settings
from sqlalchemy import Engine

if TYPE_CHECKING:
    from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    """An Alembic config pointing at the packaged migration tree.

    The tree ships inside the package rather than at the repo root, so a
    deployed wheel can migrate without the repository checked out.
    """
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url or settings.database_url)
    return config


def stamp_head(engine: Engine) -> None:
    """Mark a freshly ``create_all``-ed schema as being at the latest revision.

    ``rebuild-silver`` builds its staging schema from the models rather than by
    replaying migrations — the result is identical, and a drift test enforces
    that. Stamping is what stops the swapped-in schema from looking un-migrated
    to the next ``alembic upgrade``.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    with engine.begin() as connection:
        MigrationContext.configure(connection).stamp(script, "head")
