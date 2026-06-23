"""Generic scraping workflow.

The :class:`Scraper` receives a :class:`~composer_ingest.scraper.sources.SourceAdapter`
at construction time and orchestrates the fetch-and-store cycle without
knowing anything about the source's specific HTTP protocol or data format.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from .bucket import Bucket
from .sources import EntityDocument, SourceAdapter, SourceClaim, WorkMentionDocument


def _serialize(doc: EntityDocument | WorkMentionDocument) -> dict[str, Any]:
    # asdict recursively converts nested dataclasses (including SourceClaim) to dicts
    d = dataclasses.asdict(doc)
    # datetime is not JSON-serialisable; replace with ISO 8601 string
    d["ingested_at"] = doc.ingested_at.isoformat()
    d["_type"] = "entity" if isinstance(doc, EntityDocument) else "work_mention"
    return d


def _deserialize(d: dict[str, Any]) -> EntityDocument | WorkMentionDocument:
    kind = d.pop("_type")
    d["ingested_at"] = datetime.fromisoformat(d["ingested_at"])
    if kind == "entity":
        claims = tuple(SourceClaim(**c) for c in d.pop("claims", []))
        return EntityDocument(**d, claims=claims)
    if kind == "work_mention":
        return WorkMentionDocument(**d)
    raise ValueError(f"unknown _type in bucket record: {kind!r}")


class Scraper:
    """Generic fetch workflow; source-specific behaviour is injected via the adapter."""

    def __init__(self, adapter: SourceAdapter) -> None:
        self.adapter = adapter

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield documents directly from the adapter."""
        yield from self.adapter.fetch(max_pages)

    def fetch_to_bucket(self, bucket: Bucket, max_pages: int | None = None) -> str:
        """Fetch all records from the adapter and write them to *bucket*.

        Returns the run_id (an ISO-8601 UTC timestamp string) so the caller can
        pass it to :func:`iter_from_bucket` or the ``process`` CLI command.
        """
        run_id = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S") + "-" + uuid.uuid4().hex[:8]
        records = (_serialize(doc) for doc in self.adapter.fetch(max_pages))
        bucket.write_records(self.adapter.name, run_id, records)
        return run_id


def iter_from_bucket(
    source_name: str,
    run_id: str,
    bucket: Bucket,
) -> Iterator[EntityDocument | WorkMentionDocument]:
    """Yield typed documents previously stored by :meth:`Scraper.fetch_to_bucket`."""
    for d in bucket.read_records(source_name, run_id):
        yield _deserialize(d)
