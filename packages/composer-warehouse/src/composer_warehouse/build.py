"""Atomic database builds with a status manifest.

A build writes a fresh database into a staging area no reader can see, then
swaps it in indivisibly, so readers never see a half-built result. Progress and
outcome land in a manifest — a crashed build can never be mistaken for a
completed one. Both the gold promote step and the silver rebuild use this
machinery.

Where the staging area lives and what "swap" means depends on the backend, so
``run_build`` talks to a :class:`BuildTarget` rather than to a filesystem path.
:class:`SqliteFileTarget` builds into ``{db_path}.tmp`` and swaps with
:func:`os.replace`; the Postgres target (see :mod:`composer_warehouse.postgres`)
builds into a staging schema and swaps with ``ALTER SCHEMA … RENAME``. Both are
atomic; neither can leave a partially applied result behind.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import Field, asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol, TypeVar

from sqlalchemy import Engine, create_engine

log = logging.getLogger(__name__)


class _StatsDataclass(Protocol):
    """Any dataclass instance — ``run_build`` manifests its fields via ``asdict``."""

    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]


StatsT = TypeVar("StatsT", bound=_StatsDataclass)


@dataclass(frozen=True)
class BuildManifest:
    status: str  # running | completed | failed
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    stats: dict[str, int] = field(default_factory=dict)

    @classmethod
    def start(cls) -> BuildManifest:
        return cls(status="running", started_at=datetime.now(UTC).isoformat())

    def completed(self, stats: dict[str, int]) -> BuildManifest:
        return replace(self, status="completed", finished_at=datetime.now(UTC).isoformat(), stats=stats)

    def failed(self, error: str) -> BuildManifest:
        return replace(self, status="failed", finished_at=datetime.now(UTC).isoformat(), error=error)


def manifest_path(db_path: str | Path) -> Path:
    return Path(f"{db_path}.manifest.json")


def write_build_manifest(db_path: str | Path, manifest: BuildManifest) -> None:
    path = manifest_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), ensure_ascii=False), encoding="utf-8")


def read_build_manifest(db_path: str | Path) -> BuildManifest | None:
    path = manifest_path(db_path)
    if not path.exists():
        return None
    return BuildManifest(**json.loads(path.read_text(encoding="utf-8")))


class BuildTarget(Protocol):
    """Where a build lands, and how the finished result is swapped in.

    ``begin`` returns an engine on a staging area no reader can see; ``commit``
    makes it the live database in one indivisible step; ``abort`` throws it
    away. The manifest lives outside whatever ``commit`` replaces, so it
    survives the build it is describing.
    """

    def describe(self) -> str:
        """The live database, for logs and error messages."""
        ...

    def backend(self) -> str:
        """``sqlite`` or ``postgres`` — how the swap is performed."""
        ...

    def exists(self) -> bool:
        """Whether a built silver database is actually there to read."""
        ...

    def read_manifest(self) -> BuildManifest | None: ...

    def write_manifest(self, manifest: BuildManifest) -> None: ...

    def begin(self) -> Engine:
        """Create the staging area and return an engine bound to it."""
        ...

    def commit(self) -> None:
        """Atomically replace the live database with the staged one."""
        ...

    def abort(self) -> None:
        """Discard the staging area, leaving the live database untouched."""
        ...


@dataclass
class SqliteFileTarget:
    """A SQLite file swapped in with :func:`os.replace`."""

    path: Path
    _tmp: Path = field(init=False)
    _engine: Engine | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._tmp = Path(f"{self.path}.tmp")

    def describe(self) -> str:
        return str(self.path)

    def backend(self) -> str:
        return "sqlite"

    def exists(self) -> bool:
        return self.path.exists()

    def read_manifest(self) -> BuildManifest | None:
        return read_build_manifest(self.path)

    def write_manifest(self, manifest: BuildManifest) -> None:
        write_build_manifest(self.path, manifest)

    def begin(self) -> Engine:
        self._tmp.unlink(missing_ok=True)
        self._engine = create_engine(f"sqlite:///{self._tmp}")
        return self._engine

    def commit(self) -> None:
        self._dispose()
        os.replace(self._tmp, self.path)

    def abort(self) -> None:
        self._dispose()
        self._tmp.unlink(missing_ok=True)

    def _dispose(self) -> None:
        # Release the file handle before moving or deleting the file it points
        # at; on Windows an open handle would block both.
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None


def run_build(target: BuildTarget, build: Callable[[Engine], StatsT]) -> StatsT:
    """Run ``build`` into ``target``'s staging area and swap the result in.

    ``build`` receives an engine on the staging database and returns a stats
    dataclass; its ``asdict`` lands in the completed manifest. On failure the
    staging area is discarded, the manifest records the error, the live
    database stays untouched, and the exception propagates.
    """
    manifest = BuildManifest.start()
    target.write_manifest(manifest)
    engine = target.begin()
    try:
        stats = build(engine)
        target.commit()
    # BaseException, not Exception: a Ctrl-C during an hour-long rebuild must
    # still drop the staging area rather than orphan a half-built database.
    except BaseException as exc:
        target.abort()
        target.write_manifest(manifest.failed(f"{type(exc).__name__}: {exc}"))
        raise
    target.write_manifest(manifest.completed(asdict(stats)))
    log.info("built %s: %s", target.describe(), stats)
    return stats
