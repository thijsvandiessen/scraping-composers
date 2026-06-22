"""Bridge between sources and the raw data bucket.

dump_to_bucket   — fetch live documents from a source, serialize, store.
iter_from_bucket — deserialize stored documents back to ``Document`` objects.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from .bucket import Bucket
from .document import Document
from .sources import SourceLike


def _serialize(doc: Document) -> dict[str, Any]:
    return dataclasses.asdict(doc)


def _deserialize(d: dict[str, Any]) -> Document:
    return Document(**d)


def dump_to_bucket(
    source: SourceLike,
    bucket: Bucket,
    max_pages: int | None = None,
) -> str:
    """Fetch all documents from *source* and write them to *bucket*.

    Returns the run_id (an ISO-8601 UTC timestamp string) so the caller can
    pass it to ``iter_from_bucket`` or the ``process`` CLI command.
    """
    run_id = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S") + "-" + uuid.uuid4().hex[:8]
    records = (_serialize(doc) for doc in source.fetch_documents(max_pages=max_pages))
    bucket.write_records(source.NAME, run_id, records)
    return run_id


def iter_from_bucket(
    source_name: str,
    run_id: str,
    bucket: Bucket,
) -> Iterator[Document]:
    """Yield documents previously stored by ``dump_to_bucket``."""
    for d in bucket.read_records(source_name, run_id):
        yield _deserialize(d)
