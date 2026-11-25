"""Typed response models for the dashboard's HTTP API clients.

Each model mirrors the corresponding FastAPI response schema from ``admin-api``
or ``consumer-api``, with ``extra="ignore"`` so additive API changes never
break the dashboard.
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field


class _APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PaginatedResponse(_APIModel):
    total: int = 0
    page: int = 1
    limit: int = 20


# ---------------------------------------------------------------------------
# Admin API (/admin/v1)
# ---------------------------------------------------------------------------


class SnapshotOut(_APIModel):
    source: str
    id: str
    status: str
    kind: str
    started_at: str
    finished_at: str | None = None
    record_count: int | None = None
    size_bytes: int = 0
    error: str | None = None


class ScraperOut(_APIModel):
    name: str
    base_url: str | None = None
    cadence: str
    due: bool
    last_snapshot: SnapshotOut | None = None
    running_snapshots: list[SnapshotOut] = Field(default_factory=list)


class FetchStarted(_APIModel):
    source: str
    snapshot_id: str
    status: str


class RunStarted(_APIModel):
    run_id: int
    source: str
    status: str


class RunOut(_APIModel):
    id: int
    source: str
    status: str
    started_at: str
    finished_at: str | None = None
    records_seen: int = 0
    records_new: int = 0
    error: str | None = None


class GoldStatus(_APIModel):
    backend: str = "sqlite"
    exists: bool
    status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)


class Rule1PersonThresholds(_APIModel):
    min_concert_appearances: int
    min_recording_appearances: int
    min_appearances_for_composers: int
    min_works_for_composers: int = 1
    min_programmes_for_composers: int = 1
    min_sitelinks: int | None = None


class Rule1EnsembleThresholds(_APIModel):
    min_concert_appearances: int
    min_recording_appearances: int


class Rule1Config(_APIModel):
    persons: Rule1PersonThresholds
    ensembles: Rule1EnsembleThresholds


class PromoteOptions(TypedDict, total=False):
    gold_url: str
    min_referrers: int
    drop_unevidenced_persons: bool
    collapse_duplicates: bool
    prune_unreferenced: bool


# ---------------------------------------------------------------------------
# Consumer API (/v1 — silver & gold)
# ---------------------------------------------------------------------------


class StatsOut(_APIModel):
    entities_total: int
    entities_by_kind: dict[str, int]
    claims: int
    records: int
    records_by_source: dict[str, int]
    works: int
    work_titles: int
    work_mentions: int
    mentions_by_status: dict[str, int]
    persons_linked: int
    person_matches_to_review: int


class EntitySummary(_APIModel):
    id: str
    label: str
    kind: str
    created_at: str


class EntityPage(PaginatedResponse):
    items: list[EntitySummary] = Field(default_factory=list)


class ClaimOut(_APIModel):
    predicate: str
    value: str | None = None
    object_label: str | None = None
    object_id: str | None = None
    source: str
    source_url: str | None = None
    source_external_id: str | None = None


class IncomingClaimOut(_APIModel):
    subject_id: str
    subject_label: str
    predicate: str
    source: str


class EntityDetail(_APIModel):
    id: str
    label: str
    kind: str
    created_at: str
    canonical_entity_id: str | None = None
    claims: list[ClaimOut] = Field(default_factory=list)
    incoming_total: int = 0
    incoming: list[IncomingClaimOut] = Field(default_factory=list)


class PersonSummary(_APIModel):
    id: str
    label: str
    created_at: str
    concert_count: int = 0
    recording_count: int = 0


class PersonPage(PaginatedResponse):
    items: list[PersonSummary] = Field(default_factory=list)


class EntityRef(_APIModel):
    id: str
    label: str


class EnsembleSummary(_APIModel):
    id: str
    label: str
    wikidata_id: str | None = None
    ensemble_type: str | None = None
    home_city: EntityRef | None = None
    canonical_entity_id: str | None = None
    concert_count: int = 0
    recording_count: int = 0


class EnsemblePage(PaginatedResponse):
    items: list[EnsembleSummary] = Field(default_factory=list)


class PersonConcertOut(_APIModel):
    id: str
    source: str
    date: str | None = None
    venue: str | None = None
    season: str | None = None
    url: str | None = None
    role: str
    works: list[str] = Field(default_factory=list)


class PersonConcertsPage(PaginatedResponse):
    person_id: str
    person_label: str
    items: list[PersonConcertOut] = Field(default_factory=list)


class ConcertSummary(_APIModel):
    id: str
    source: str
    date: str | None = None
    venue: str | None = None
    season: str | None = None
    event_type: str | None = None
    url: str | None = None
    conductors: list[str] = Field(default_factory=list)
    soloist_count: int = 0
    work_count: int = 0


class ConcertListPage(PaginatedResponse):
    items: list[ConcertSummary] = Field(default_factory=list)


class EventParticipantOut(_APIModel):
    role: str
    name: str
    discipline: str | None = None
    entity_id: str | None = None


class EventWorkOut(_APIModel):
    title: str
    composer: str | None = None


class ConcertDetail(_APIModel):
    id: str
    source: str
    date: str | None = None
    venue: str | None = None
    season: str | None = None
    event_type: str | None = None
    url: str | None = None
    participants: list[EventParticipantOut] = Field(default_factory=list)
    works: list[EventWorkOut] = Field(default_factory=list)


class PersonRecordingOut(_APIModel):
    id: str
    source: str
    title: str | None = None
    release_date: str | None = None
    label: str | None = None
    catalogue_number: str | None = None
    format: str | None = None
    url: str | None = None
    role: str
    works: list[str] = Field(default_factory=list)


class PersonRecordingsPage(PaginatedResponse):
    person_id: str
    person_label: str
    items: list[PersonRecordingOut] = Field(default_factory=list)


class RecordingSummary(_APIModel):
    id: str
    source: str
    title: str | None = None
    release_date: str | None = None
    label: str | None = None
    catalogue_number: str | None = None
    format: str | None = None
    url: str | None = None
    conductors: list[str] = Field(default_factory=list)
    performer_count: int = 0
    work_count: int = 0


class RecordingListPage(PaginatedResponse):
    items: list[RecordingSummary] = Field(default_factory=list)


class RecordingDetail(_APIModel):
    id: str
    source: str
    title: str | None = None
    release_date: str | None = None
    label: str | None = None
    catalogue_number: str | None = None
    format: str | None = None
    url: str | None = None
    participants: list[EventParticipantOut] = Field(default_factory=list)
    works: list[EventWorkOut] = Field(default_factory=list)


class WorkIdentifierOut(_APIModel):
    scheme: str
    value: str


class WorkProofOut(_APIModel):
    source: str
    source_url: str | None = None
    date: str | None = None
    venue: str | None = None


class WorkLinkOut(_APIModel):
    relation: str
    work_id: str
    title: str


class WorkSummary(_APIModel):
    id: str
    canonical_title: str
    composer_id: str | None = None
    composer_label: str | None = None
    work_type: str | None = None
    opus_number: str | None = None
    catalogue: str | None = None
    identifiers: list[WorkIdentifierOut] = Field(default_factory=list)
    wikidata_id: str | None = None
    musical_key: str | None = None
    number: int | None = None
    premiere_date: str | None = None
    mention_count: int = 0
    aliases: list[str] = Field(default_factory=list)


class WorkPage(PaginatedResponse):
    items: list[WorkSummary] = Field(default_factory=list)


class WorkDetail(WorkSummary):
    premiere_place: str | None = None
    proof: list[WorkProofOut] = Field(default_factory=list)
    links: list[WorkLinkOut] = Field(default_factory=list)
    derived: list[WorkLinkOut] = Field(default_factory=list)


class MentionOut(_APIModel):
    id: int
    source: str
    composer: str | None = None
    title: str
    status: str
    score: float | None = None
    method: str | None = None
    work_id: str | None = None
    work_title: str | None = None
    candidate_work_id: str | None = None
    candidate_title: str | None = None


class MentionPage(PaginatedResponse):
    items: list[MentionOut] = Field(default_factory=list)
