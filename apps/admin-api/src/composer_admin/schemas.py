from datetime import datetime

from pydantic import BaseModel


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


class FetchStarted(BaseModel):
    source: str
    snapshot_id: str
    status: str


class RunStarted(BaseModel):
    run_id: int
    source: str
    status: str


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
