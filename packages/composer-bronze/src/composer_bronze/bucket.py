"""Raw data bucket: store and retrieve serialized source records.

LocalBucket writes NDJSON files under a local directory tree:

    {root}/{source_name}/{run_id}/records.ndjson
    {root}/{source_name}/{run_id}/manifest.json

Each NDJSON line is a JSON object with a ``_type`` field (``"entity"`` or
``"work_mention"``) followed by the fields of the document. The manifest
records the fetch's status (``running``/``completed``/``failed``) and record
count, so readers can tell a complete snapshot from one whose fetch crashed
mid-write. Replacing ``LocalBucket`` with an ``S3Bucket`` implementation later
requires no changes to the callers.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from composer_config import settings

DEFAULT_BUCKET_PATH = settings.bucket_path

MANIFEST_FILENAME = "manifest.json"

# Snapshot statuses. "unknown" is never stored: it is synthesized for legacy
# snapshot dirs that predate the manifest.
LOADABLE_STATUSES = ("completed", "unknown")

# Record ``_type`` values that make a snapshot loadable into the warehouse.
# A crawl source's dir mixes these "documents" snapshots (written by scrape or
# the LLM ``extract`` step) with raw "pages" snapshots (``_type: "crawl"``);
# only the former can be ingested — see ``Snapshot.kind``.
DOCUMENT_RECORD_TYPES = frozenset({"entity", "work_mention"})


def _validated_segment(value: str, field: str) -> str:
    """Require *value* to be a single path segment, guarding against traversal (CWE-22).

    ``source`` and ``run_id`` may come from untrusted input (API path
    parameters, CLI arguments); joined onto the bucket root unchecked, a value
    like ``../../etc`` or an absolute path would escape the bucket.
    """
    if (
        not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or os.path.basename(value) != value
    ):
        raise ValueError(f"invalid {field} {value!r}: must be a single path segment")
    return value


@dataclass(frozen=True)
class SnapshotManifest:
    source: str
    run_id: str
    status: str  # running | completed | failed (synthesized: unknown)
    started_at: str  # ISO 8601 UTC
    finished_at: str | None = None
    record_count: int | None = None
    error: str | None = None

    @classmethod
    def start(cls, source: str, run_id: str) -> SnapshotManifest:
        return cls(
            source=source,
            run_id=run_id,
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )

    def completed(self, record_count: int) -> SnapshotManifest:
        return replace(
            self,
            status="completed",
            finished_at=datetime.now(UTC).isoformat(),
            record_count=record_count,
        )

    def failed(self, error: str, record_count: int | None = None) -> SnapshotManifest:
        return replace(
            self,
            status="failed",
            finished_at=datetime.now(UTC).isoformat(),
            record_count=record_count,
            error=error,
        )


@dataclass(frozen=True)
class Snapshot:
    """A snapshot as listed from the bucket: its manifest plus file stats."""

    manifest: SnapshotManifest
    size_bytes: int
    kind: str  # "documents" (loadable) | "pages" (raw crawl, extract first)


class Bucket(Protocol):
    def write_records(self, source: str, run_id: str, records: Iterable[dict[str, Any]]) -> None: ...
    def read_records(self, source: str, run_id: str) -> Iterator[dict[str, Any]]: ...
    def list_runs(self, source: str) -> list[str]: ...
    def list_sources(self) -> list[str]: ...
    def write_manifest(self, manifest: SnapshotManifest) -> None: ...
    def read_manifest(self, source: str, run_id: str) -> SnapshotManifest | None: ...
    def list_snapshots(self, source: str) -> list[Snapshot]: ...


class LocalBucket:
    """Bucket backed by the local filesystem."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _run_dir(self, source: str, run_id: str) -> Path:
        _validated_segment(source, "source")
        _validated_segment(run_id, "run_id")
        base_path = os.path.abspath(self.root)
        fullpath = os.path.normpath(os.path.join(base_path, source, run_id))
        if not fullpath.startswith(base_path):
            raise ValueError(f"Path traversal detected: {fullpath}")
        return Path(fullpath)

    def _ndjson_path(self, source: str, run_id: str) -> Path:
        return self._run_dir(source, run_id) / "records.ndjson"

    def write_records(self, source: str, run_id: str, records: Iterable[dict[str, Any]]) -> None:
        path = self._ndjson_path(source, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False))
                fh.write("\n")
                # Flushed per record because *records* is usually a generator
                # driving a live fetch or crawl: a run killed outright (SIGTERM
                # unwinds nothing) would otherwise lose whatever sat in the
                # buffer. One small write per record, against a network fetch.
                fh.flush()

    def read_records(self, source: str, run_id: str) -> Iterator[dict[str, Any]]:
        path = self._ndjson_path(source, run_id)
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def list_runs(self, source: str) -> list[str]:
        _validated_segment(source, "source")
        base_path = os.path.abspath(self.root)
        source_dir = os.path.normpath(os.path.join(base_path, source))
        if not source_dir.startswith(base_path):
            raise ValueError(f"Path traversal detected: {source_dir}")
        source_dir_path = Path(source_dir)
        if not source_dir_path.is_dir():
            return []
        return sorted(p.name for p in source_dir_path.iterdir() if p.is_dir())

    def list_sources(self) -> list[str]:
        """Every source with data in the bucket (each a top-level dir), sorted.

        Enumerating the bucket itself — rather than a registry — surfaces
        crawl-config sources and even sources whose config was later deleted.
        """
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def write_manifest(self, manifest: SnapshotManifest) -> None:
        run_dir = self._run_dir(manifest.source, manifest.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / MANIFEST_FILENAME).write_text(
            json.dumps(asdict(manifest), ensure_ascii=False), encoding="utf-8"
        )

    def read_manifest(self, source: str, run_id: str) -> SnapshotManifest | None:
        path = self._run_dir(source, run_id) / MANIFEST_FILENAME
        if not path.exists():
            return None
        return SnapshotManifest(**json.loads(path.read_text(encoding="utf-8")))

    def _fallback_manifest(self, source: str, run_id: str) -> SnapshotManifest:
        """Synthesize a manifest for a legacy snapshot dir written before manifests."""
        ndjson = self._ndjson_path(source, run_id)
        mtime = datetime.fromtimestamp(ndjson.stat().st_mtime, tz=UTC) if ndjson.exists() else None
        return SnapshotManifest(
            source=source,
            run_id=run_id,
            status="unknown",
            started_at=mtime.isoformat() if mtime else "",
            finished_at=mtime.isoformat() if mtime else None,
        )

    def _snapshot_kind(self, source: str, run_id: str) -> str:
        """Classify a snapshot by peeking its first record's ``_type``.

        Cheap — reads a single line. An empty or unreadable snapshot (and any
        raw-page snapshot) is "pages": not loadable, so nothing is lost by the
        default.
        """
        try:
            first = next(self.read_records(source, run_id), None)
        except (OSError, ValueError):
            return "pages"
        if first is not None and first.get("_type") in DOCUMENT_RECORD_TYPES:
            return "documents"
        return "pages"

    def list_snapshots(self, source: str) -> list[Snapshot]:
        snapshots = []
        for run_id in self.list_runs(source):
            manifest = self.read_manifest(source, run_id) or self._fallback_manifest(source, run_id)
            ndjson = self._ndjson_path(source, run_id)
            size = ndjson.stat().st_size if ndjson.exists() else 0
            kind = self._snapshot_kind(source, run_id)
            snapshots.append(Snapshot(manifest=manifest, size_bytes=size, kind=kind))
        return snapshots


def latest_loadable_run_id(bucket: Bucket, source: str) -> str | None:
    """The most recent snapshot of *source* worth reading, or None if there is none.

    Skips fetches that are still running or crashed, so callers defaulting to "the
    latest snapshot" never pick up a half-written one.
    """
    loadable = [
        snapshot.manifest.run_id
        for snapshot in bucket.list_snapshots(source)
        if snapshot.manifest.status in LOADABLE_STATUSES
    ]
    return loadable[-1] if loadable else None


def latest_document_run_id(bucket: Bucket, source: str) -> str | None:
    """The most recent loadable *documents* snapshot of *source*, or None.

    Like :func:`latest_loadable_run_id` but skips raw-page crawl snapshots, so a
    crawl re-run after an extract never shadows the extracted documents.
    """
    documents = [
        snapshot.manifest.run_id
        for snapshot in bucket.list_snapshots(source)
        if snapshot.manifest.status in LOADABLE_STATUSES and snapshot.kind == "documents"
    ]
    return documents[-1] if documents else None
