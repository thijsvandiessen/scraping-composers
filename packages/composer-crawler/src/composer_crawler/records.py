"""The raw record a crawl stores: one fetched response plus its metadata."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from composer_bronze.bucket import Bucket

RECORD_TYPE = "crawl"

# Response headers worth keeping for later conditional refetches and parsing.
_KEPT_HEADERS = ("etag", "last-modified", "content-type", "content-language")


@dataclass(frozen=True)
class CrawlRecord:
    """One fetched page or API response, as stored in the bronze bucket."""

    url: str
    final_url: str
    status_code: int
    content_type: str | None
    fetched_at: str
    depth: int
    headers: dict[str, str]
    # crawl4ai's main-content markdown (fit_markdown) — the page as stored. The raw
    # HTML is deliberately not kept: it is ~60x larger and nothing reads it.
    markdown: str = ""
    # Page metadata (title, description, og:*, keywords, lang, ...) kept alongside
    # the markdown so nothing from the HTML head is lost when the LLM reads markdown.
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["_type"] = RECORD_TYPE
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CrawlRecord:
        """Rebuild a record, ignoring fields this version no longer keeps.

        Snapshots outlive the schema: older ones still carry the ``body`` HTML that
        records no longer store, so unknown keys are dropped rather than raising.
        """
        kind = d.get("_type")
        if kind != RECORD_TYPE:
            raise ValueError(f"unknown _type in crawl record: {kind!r}")
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def kept_headers(headers: Any) -> dict[str, str]:
    """Subset of response headers worth persisting (present ones only)."""
    return {name: headers[name] for name in _KEPT_HEADERS if name in headers}


def iter_crawl_records(source_name: str, run_id: str, bucket: Bucket) -> Iterator[CrawlRecord]:
    """Yield typed records previously stored by :meth:`Crawler.crawl_to_bucket`."""
    for d in bucket.read_records(source_name, run_id):
        yield CrawlRecord.from_dict(d)
