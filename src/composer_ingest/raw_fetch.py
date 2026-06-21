"""Bridge between source modules and the raw data bucket.

dump_to_bucket   — fetch live records from a source, serialize, store.
iter_from_bucket — deserialize stored records back to typed objects.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from .bucket import Bucket
from .sources import SourceClaim, SourceLike, SourceRecord, SourceWorkMention


def _serialize(item: SourceRecord | SourceWorkMention) -> dict[str, Any]:
    d = dataclasses.asdict(item)
    if isinstance(item, SourceRecord):
        d["_type"] = "record"
    else:
        d["_type"] = "work_mention"
    return d


def _deserialize(d: dict[str, Any]) -> SourceRecord | SourceWorkMention:
    kind = d.pop("_type")
    if kind == "record":
        claims = tuple(SourceClaim(**c) for c in d.pop("claims", []))
        return SourceRecord(**d, claims=claims)
    raw_str = d.get("raw")
    if isinstance(raw_str, str):
        d["raw"] = json.loads(raw_str)
    return SourceWorkMention(**d)


def dump_to_bucket(
    source: SourceLike,
    bucket: Bucket,
    max_pages: int | None = None,
) -> str:
    """Fetch all records from *source* and write them to *bucket*.

    Returns the run_id (an ISO-8601 UTC timestamp string) so the caller can
    pass it to ``iter_from_bucket`` or ``process`` CLI command.
    """
    run_id = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    records = (_serialize(item) for item in source.fetch_records(max_pages=max_pages))
    bucket.write_records(source.NAME, run_id, records)
    return run_id


def iter_from_bucket(
    source_name: str,
    run_id: str,
    bucket: Bucket,
) -> Iterator[SourceRecord | SourceWorkMention]:
    """Yield typed records previously stored by ``dump_to_bucket``."""
    for d in bucket.read_records(source_name, run_id):
        yield _deserialize(d)
