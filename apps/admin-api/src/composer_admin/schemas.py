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
    kind: str  # documents (loadable) | pages (retired crawler output, never loadable)
    started_at: str
    finished_at: str | None
    record_count: int | None
    size_bytes: int
    error: str | None


class ScraperOut(BaseModel):
    name: str
    base_url: str | None
    cadence: str  # yearly | static (weekly | monthly unused by the adapters)
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


class PromoteOptions(BaseModel):
    """Optional per-run promotion settings; an omitted field means its default.

    ``min_referrers`` distinguishes omitted (use the server's configured
    default) from an explicit value via ``model_fields_set``. Rule 1's
    concert/recording/composer/sitelink thresholds are not settable here — the
    server always applies its ``rule1_config.json``; read or replace it via
    ``GET``/``PUT /admin/v1/rule1-config`` (see ``Rule1ConfigBody``).
    """

    gold_url: str | None = None  # None: the server's GOLD_DATABASE_URL
    min_referrers: int = Field(default=1, ge=1)  # rule 3 threshold
    drop_unevidenced_persons: bool = True  # rule 1
    collapse_duplicates: bool = True  # rule 2
    prune_unreferenced: bool = True  # rule 3


class Rule1PersonThresholds(BaseModel):
    min_concert_appearances: int = Field(ge=0)
    min_recording_appearances: int = Field(ge=0)
    min_appearances_for_composers: int = Field(ge=0)
    min_works_for_composers: int = Field(default=1, ge=1)
    min_programmes_for_composers: int = Field(default=1, ge=1)
    min_sitelinks: int | None = Field(default=None, ge=0)


class Rule1EnsembleThresholds(BaseModel):
    min_concert_appearances: int = Field(ge=0)
    min_recording_appearances: int = Field(ge=0)


class Rule1ConfigBody(BaseModel):
    """Rule 1's concert/recording/composer/sitelink thresholds (see
    ``composer_gold.Rule1Config``). ``GET`` returns the server's current
    ``rule1_config.json``; ``PUT`` replaces it wholesale and takes effect on
    the next promote."""

    persons: Rule1PersonThresholds
    ensembles: Rule1EnsembleThresholds


class BuildStatus(BaseModel):
    """A derived database's state: its last build, stats and current activity."""

    backend: str  # sqlite | postgres | unsupported — how the atomic swap is performed
    # whether it has actually been built: the file is present (sqlite) or the
    # schema holds the tables (postgres)
    exists: bool
    status: str | None  # running | completed | failed | None (never built)
    started_at: str | None
    finished_at: str | None
    error: str | None
    stats: dict[str, int]


class GoldStatus(BuildStatus):
    pass


class SilverStatus(BuildStatus):
    pass
