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


class ScraperOut(BaseModel):
    name: str
    base_url: str | None
    cadence: str  # monthly | yearly | static
    due: bool  # stale enough to be worth re-scraping now
    last_run: RunOut | None


class RunStarted(BaseModel):
    run_id: int
    source: str
    status: str
