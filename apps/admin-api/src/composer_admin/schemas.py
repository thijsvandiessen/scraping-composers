from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class RunOut(BaseModel):
    id: int
    source: str
    status: str  # running | completed | failed
    started_at: datetime
    finished_at: datetime | None
    records_seen: int
    records_new: int
    error: str | None


class SnapshotOut(BaseModel):
    source: str
    id: str  # bucket run_id, e.g. "2026-07-02T09:52:30-3086f07d"
    status: str  # running | completed | failed | unknown (pre-manifest snapshot)
    started_at: str
    finished_at: str | None
    record_count: int | None
    size_bytes: int
    error: str | None


class ScraperOut(BaseModel):
    name: str
    base_url: str | None
    cadence: str  # monthly | yearly | static
    due: bool  # raw data stale enough to be worth re-fetching now
    last_snapshot: SnapshotOut | None


class PageParamIn(BaseModel):
    """Pagination by incrementing a query parameter."""

    type: Literal["page_param"] = "page_param"
    param: str = "page"
    start: int = 1


class NextUrlFromJsonIn(BaseModel):
    """API pagination following a URL at a dot-path in the JSON body."""

    type: Literal["next_url_from_json"] = "next_url_from_json"
    pointer: str


PaginationIn = Annotated[PageParamIn | NextUrlFromJsonIn, Field(discriminator="type")]


class CrawlConfigIn(BaseModel):
    """A crawl config as edited in the dashboard; the name comes from the URL.

    ``headers`` and ``timeout_s`` are not editable here and keep the
    ``CrawlConfig`` defaults; cross-field rules (follow_links needs an allow
    pattern, patterns must compile) are enforced by ``CrawlConfig`` itself.
    """

    seeds: list[str] = Field(min_length=1)
    follow_links: bool = False
    allow_patterns: list[str] = []
    max_depth: int = Field(default=2, ge=0)
    max_pages: int | None = Field(default=None, ge=1)
    pagination: PaginationIn | None = None
    request_delay_s: float = Field(default=0.5, ge=0)
    respect_robots: bool = True


class CrawlOut(BaseModel):
    name: str
    seeds: list[str]
    follow_links: bool
    allow_patterns: list[str]
    max_depth: int
    max_pages: int | None
    pagination: PaginationIn | None
    request_delay_s: float
    respect_robots: bool
    editable: bool  # False for code-registered configs (edit those in the source tree)
    last_snapshot: SnapshotOut | None  # crawl runs are bucket snapshots


class FetchStarted(BaseModel):
    source: str
    snapshot_id: str
    status: str


class RunStarted(BaseModel):
    run_id: int
    source: str
    status: str


class PromoteOptions(BaseModel):
    """Optional per-run promotion settings; an omitted field means its default.

    ``min_sitelinks`` distinguishes omitted (use the server's configured
    default) from an explicit ``null`` (turn the sitelink signal off) via
    ``model_fields_set``.
    """

    gold_path: str | None = None  # None: the server's configured gold path
    min_sitelinks: int | None = Field(default=None, ge=0)
    drop_unevidenced_persons: bool = True  # rule 1
    collapse_duplicates: bool = True  # rule 2
    prune_unreferenced: bool = True  # rule 3


class GoldStatus(BaseModel):
    exists: bool  # whether the gold database file is present
    status: str | None  # running | completed | failed | None (never promoted)
    started_at: str | None
    finished_at: str | None
    error: str | None
    stats: dict[str, int]


class SilverStatus(BaseModel):
    exists: bool  # whether the silver database file is present (False when not sqlite)
    status: str | None  # running | completed | failed | None (never rebuilt)
    started_at: str | None
    finished_at: str | None
    error: str | None
    stats: dict[str, int]
