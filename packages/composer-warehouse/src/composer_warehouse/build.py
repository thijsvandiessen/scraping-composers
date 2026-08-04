"""Atomic database builds with a status manifest.

A build writes a fresh database into ``{db_path}.tmp`` and atomically swaps it
in with :func:`os.replace`, so readers never see a half-built file. Progress
and outcome land in ``{db_path}.manifest.json`` — a crashed build can never be
mistaken for a completed one. Both the gold promote step and the silver
rebuild use this machinery.
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


def run_tracked(key: str | Path, work: Callable[[], StatsT]) -> StatsT:
    """Run ``work``, recording its progress in ``{key}.manifest.json``.

    ``work`` returns a stats dataclass whose ``asdict`` lands in the completed
    manifest. On failure the manifest records the error and the exception
    propagates, so a crashed run can never be mistaken for a completed one.

    The manifest is the whole contract here, which is why this is separate from
    :func:`run_build`: an export to an external store has the same status
    lifecycle but no local file to swap into place.
    """
    manifest = BuildManifest.start()
    write_build_manifest(key, manifest)
    try:
        stats = work()
    except Exception as exc:
        write_build_manifest(key, manifest.failed(f"{type(exc).__name__}: {exc}"))
        raise
    write_build_manifest(key, manifest.completed(asdict(stats)))
    log.info("built %s: %s", key, stats)
    return stats


def run_build(db_path: str | Path, build: Callable[[Path], StatsT]) -> StatsT:
    """Run ``build`` into ``{db_path}.tmp`` and atomically swap the result in.

    ``build`` receives the temporary path and returns a stats dataclass; its
    ``asdict`` lands in the completed manifest. On failure the manifest records
    the error, the previous database (if any) stays in place, and the exception
    propagates.
    """

    def _build_and_swap() -> StatsT:
        stats = build(Path(f"{db_path}.tmp"))
        os.replace(f"{db_path}.tmp", db_path)
        return stats

    return run_tracked(db_path, _build_and_swap)
