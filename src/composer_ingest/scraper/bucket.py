"""Raw data bucket: store and retrieve serialized source records.

LocalBucket writes NDJSON files under a local directory tree:

    {root}/{source_name}/{run_id}/records.ndjson

Each line is a JSON object with a ``_type`` field (``"record"`` or
``"work_mention"``) followed by the fields of ``SourceRecord`` or
``SourceWorkMention``.  Replacing ``LocalBucket`` with an ``S3Bucket``
implementation later requires no changes to the callers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Protocol


class Bucket(Protocol):
    def write_records(self, source: str, run_id: str, records: Iterable[dict[str, Any]]) -> None: ...
    def read_records(self, source: str, run_id: str) -> Iterator[dict[str, Any]]: ...
    def list_runs(self, source: str) -> list[str]: ...


class LocalBucket:
    """Bucket backed by the local filesystem."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _run_dir(self, source: str, run_id: str) -> Path:
        return self.root / source / run_id

    def _ndjson_path(self, source: str, run_id: str) -> Path:
        return self._run_dir(source, run_id) / "records.ndjson"

    def write_records(self, source: str, run_id: str, records: Iterable[dict[str, Any]]) -> None:
        path = self._ndjson_path(source, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False))
                fh.write("\n")

    def read_records(self, source: str, run_id: str) -> Iterator[dict[str, Any]]:
        path = self._ndjson_path(source, run_id)
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def list_runs(self, source: str) -> list[str]:
        source_dir = self.root / source
        if not source_dir.is_dir():
            return []
        return sorted(p.name for p in source_dir.iterdir() if p.is_dir())
