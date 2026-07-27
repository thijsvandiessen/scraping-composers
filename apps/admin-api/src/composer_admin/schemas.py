from datetime import datetime

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
    kind: str  # documents (loadable) | pages (raw crawl, extract first)
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


class CrawlConfigIn(BaseModel):
    """A crawl config as edited in the dashboard; the name comes from the URL.

    ``headers`` and ``timeout_s`` are not editable here and keep the
    ``CrawlConfig`` defaults; cross-field rules (follow_links needs an allow
    pattern) are enforced by ``CrawlConfig`` itself.
    """

    seeds: list[str] = Field(min_length=1)
    use_sitemap: bool = True
    use_common_crawl: bool = False
    allow_patterns: list[str] = []
    relevance_query: str | None = None
    score_threshold: float = Field(default=0.0, ge=0)
    follow_links: bool = False
    max_depth: int = Field(default=2, ge=0)
    max_pages: int | None = Field(default=None, ge=1)
    excluded_selector: str | None = None  # extra CSS to drop before markdown generation
    request_delay_s: float = Field(default=0.5, ge=0)
    respect_robots: bool = True
    extract_kind: str = "concerts"  # which LLM schema `extract` applies: concerts | recordings


class CrawlOut(BaseModel):
    name: str
    seeds: list[str]
    use_sitemap: bool
    use_common_crawl: bool
    allow_patterns: list[str]
    relevance_query: str | None
    score_threshold: float
    follow_links: bool
    max_depth: int
    max_pages: int | None
    excluded_selector: str | None
    request_delay_s: float
    respect_robots: bool
    extract_kind: str  # which LLM schema `extract` applies: concerts | recordings
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
    ``model_fields_set``; ``min_referrers`` falls back to the server default
    the same way when omitted.
    """

    gold_path: str | None = None  # None: the server's configured gold path
    min_sitelinks: int | None = Field(default=None, ge=0)
    min_referrers: int = Field(default=1, ge=1)  # rule 3 threshold
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
