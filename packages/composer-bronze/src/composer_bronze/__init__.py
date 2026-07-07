"""Bronze tier: raw source records in a bucket, plus the fetch orchestration.

A :class:`~composer_bronze.scraper.Scraper` drives any
:class:`~composer_schema.SourceAdapter` and streams its documents into a
:class:`~composer_bronze.bucket.Bucket` as NDJSON, recording each fetch with a
:class:`~composer_bronze.bucket.SnapshotManifest`.
"""

from .bucket import (
    DEFAULT_BUCKET_PATH,
    LOADABLE_STATUSES,
    Bucket,
    LocalBucket,
    Snapshot,
    SnapshotManifest,
)
from .scraper import Scraper, iter_from_bucket, new_snapshot_id

__all__ = [
    "DEFAULT_BUCKET_PATH",
    "LOADABLE_STATUSES",
    "Bucket",
    "LocalBucket",
    "Scraper",
    "Snapshot",
    "SnapshotManifest",
    "iter_from_bucket",
    "new_snapshot_id",
]
