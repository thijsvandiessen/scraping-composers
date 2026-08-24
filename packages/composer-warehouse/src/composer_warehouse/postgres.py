"""The Postgres analogue of the SQLite file swap: a schema rename.

Postgres DDL is transactional, so renaming two schemas inside one transaction
is exactly as indivisible as ``os.replace`` on a file. A build creates a
staging schema, fills it for however long that takes, then in a final
sub-millisecond transaction demotes the live schema and promotes the staging
one into its place.

Two design choices are worth stating, because both look like details and are
not:

**A dedicated schema, never ``public``.** Renaming ``public`` needs ownership
of it (``pg_database_owner`` since PG 15), which fails on any shared or
least-privilege database; and ``public`` is where extensions install by
default, so dropping the demoted copy would take them with it. Silver gets its
own schema, and the swap can only ever touch schemas it created.

**The previous ``_old`` schema is dropped at the start of a build, not during
the swap.** ``DROP SCHEMA ... CASCADE`` takes an ``AccessExclusiveLock`` on
every relation in it and queues behind any live reader. Doing that inside the
swap transaction means the swap can block while holding the catalog locks it
just took. Hoisting it to build start costs nothing — it happens an hour
earlier — and reduces the swap itself to two catalog rows.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field

from composer_models.db import get_engine
from sqlalchemy import URL, Connection, Engine, text
from sqlalchemy.exc import OperationalError

from .build import BuildManifest

log = logging.getLogger(__name__)

# One rebuild at a time per database. The value is arbitrary but must stay
# fixed: it is the identity of the lock, not a payload.
_REBUILD_LOCK_KEY = 0x51_1E_00_01

_META_SCHEMA = "composer_meta"


@dataclass
class PostgresSchemaTarget:
    """A Postgres schema swapped in with ``ALTER SCHEMA … RENAME``."""

    url: URL
    live: str

    _staging: str = field(init=False, default="")
    _admin: Engine | None = field(init=False, default=None)
    _staging_engine: Engine | None = field(init=False, default=None)
    _lock_connection: Connection | None = field(init=False, default=None)

    @property
    def _demoted(self) -> str:
        return f"{self.live}_old"

    def describe(self) -> str:
        return f"{self.url.render_as_string()} schema {self.live!r}"

    def backend(self) -> str:
        return "postgres"

    # -- lifecycle ---------------------------------------------------------

    def begin(self) -> Engine:
        self._staging = f"{self.live}_build_{uuid.uuid4().hex[:8]}"
        admin = self._admin_engine()

        # A session-scoped advisory lock, not a manifest check: it is a real
        # mutex, and it is released even if this process is killed.
        connection = admin.connect()
        if not connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _REBUILD_LOCK_KEY}):
            connection.close()
            raise RuntimeError(f"a rebuild of {self.live!r} is already in progress")
        self._lock_connection = connection

        with admin.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{self._demoted}" CASCADE'))
            conn.execute(text(f'CREATE SCHEMA "{self._staging}"'))

        self._staging_engine = get_engine(
            self.url.render_as_string(hide_password=False), schema=self._staging
        )
        return self._staging_engine

    def commit(self) -> None:
        # The staging engine's connections are pinned to a schema that is about
        # to stop existing under that name; drop them before the rename.
        self._dispose_staging()
        admin = self._admin_engine()
        with admin.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            if self._schema_exists(conn, self.live):
                conn.execute(text(f'ALTER SCHEMA "{self.live}" RENAME TO "{self._demoted}"'))
            conn.execute(text(f'ALTER SCHEMA "{self._staging}" RENAME TO "{self.live}"'))
        self._drop_demoted()
        self._release()

    def abort(self) -> None:
        self._dispose_staging()
        try:
            admin = self._admin_engine()
            with admin.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{self._staging}" CASCADE'))
        except OperationalError:
            log.warning("could not drop staging schema %s; the next rebuild will", self._staging)
        self._release()

    # -- manifest ----------------------------------------------------------

    def read_manifest(self) -> BuildManifest | None:
        admin = self._admin_engine()
        with admin.begin() as conn:
            self._ensure_manifest_table(conn)
            row = conn.execute(
                text(
                    "SELECT status, started_at, finished_at, error, stats"
                    f" FROM {_META_SCHEMA}.build_manifest WHERE target = :target"
                ),
                {"target": self.live},
            ).first()
        if row is None:
            return None
        status, started_at, finished_at, error, stats = row
        return BuildManifest(
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            error=error,
            stats=json.loads(stats),
        )

    def write_manifest(self, manifest: BuildManifest) -> None:
        # Its own connection and its own transaction, never the staging one:
        # the failure manifest is written when the build's transaction is
        # already aborted, and any statement on that connection would raise.
        admin = self._admin_engine()
        with admin.begin() as conn:
            self._ensure_manifest_table(conn)
            conn.execute(
                text(
                    f"INSERT INTO {_META_SCHEMA}.build_manifest"
                    " (target, status, started_at, finished_at, error, stats)"
                    " VALUES (:target, :status, :started_at, :finished_at, :error, :stats)"
                    " ON CONFLICT (target) DO UPDATE SET"
                    " status = EXCLUDED.status, started_at = EXCLUDED.started_at,"
                    " finished_at = EXCLUDED.finished_at, error = EXCLUDED.error,"
                    " stats = EXCLUDED.stats"
                ),
                {"target": self.live, **asdict(manifest), "stats": json.dumps(manifest.stats)},
            )

    def exists(self) -> bool:
        """Whether the live schema holds a built database, not just a name."""
        admin = self._admin_engine()
        with admin.connect() as conn:
            return conn.scalar(text(f"SELECT to_regclass('{self.live}.entities')")) is not None

    # -- internals ---------------------------------------------------------

    def _admin_engine(self) -> Engine:
        """An engine that is *not* pinned to the silver schema.

        Its statements are all schema-qualified, so it never depends on a
        schema existing — which is what lets it create the first one.
        """
        if self._admin is None:
            self._admin = get_engine(self.url.render_as_string(hide_password=False), schema="public")
        return self._admin

    @staticmethod
    def _ensure_manifest_table(conn: Connection) -> None:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_META_SCHEMA}"'))
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_META_SCHEMA}.build_manifest ("
                " target text PRIMARY KEY,"
                " status text NOT NULL,"
                " started_at text NOT NULL,"
                " finished_at text,"
                " error text,"
                " stats text NOT NULL DEFAULT '{}')"
            )
        )

    @staticmethod
    def _schema_exists(conn: Connection, name: str) -> bool:
        return (
            conn.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": name}) is not None
        )

    def _drop_demoted(self) -> None:
        # Best effort: a reader still on the old tables holds it open, and the
        # next rebuild starts by dropping it anyway.
        try:
            admin = self._admin_engine()
            with admin.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{self._demoted}" CASCADE'))
        except OperationalError:
            log.info("%s is still in use; the next rebuild will drop it", self._demoted)

    def _dispose_staging(self) -> None:
        if self._staging_engine is not None:
            self._staging_engine.dispose()
            self._staging_engine = None

    def _release(self) -> None:
        if self._lock_connection is not None:
            self._lock_connection.close()  # releases the advisory lock
        self._lock_connection = None
        if self._admin is not None:
            self._admin.dispose()
            self._admin = None
