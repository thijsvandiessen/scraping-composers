import uuid
from datetime import datetime

from pydantic import BaseModel


class ComposerSummary(BaseModel):
    id: uuid.UUID
    label: str
    created_at: datetime
    concert_count: int = 0  # concerts participated in (populated in gold)
    # Wikipedia language editions with an article on the person (from the
    # Wikidata sitelink_count claim); None when the person has no such claim,
    # which is different from a claimed count of 0.
    sitelink_count: int | None = None

    model_config = {"from_attributes": True}


class ClaimOut(BaseModel):
    predicate: str
    value: str | None
    object_label: str | None
    object_id: uuid.UUID | None = None
    source: str
    # The exact source page the claim came from (e.g. https://www.wikidata.org/wiki/Q255),
    # falling back to the source homepage when the record carries no URL.
    source_url: str | None
    source_external_id: str | None = None  # the source's own id for the subject (e.g. wikidata QID)


class ComposerDetail(BaseModel):
    id: uuid.UUID
    label: str
    kind: str
    created_at: datetime
    claims: list[ClaimOut]


class ComposerPage(BaseModel):
    items: list[ComposerSummary]
    total: int
    page: int
    limit: int


class StatsOut(BaseModel):
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


class EntitySummary(BaseModel):
    id: uuid.UUID
    label: str
    kind: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EntityPage(BaseModel):
    items: list[EntitySummary]
    total: int
    page: int
    limit: int


class IncomingClaimOut(BaseModel):
    subject_id: uuid.UUID
    subject_label: str
    predicate: str
    source: str


class EntityDetail(BaseModel):
    id: uuid.UUID
    label: str
    kind: str
    created_at: datetime
    canonical_entity_id: uuid.UUID | None
    claims: list[ClaimOut]
    incoming_total: int
    incoming: list[IncomingClaimOut]


class WorkSummary(BaseModel):
    id: uuid.UUID
    canonical_title: str
    composer_id: uuid.UUID | None
    composer_label: str | None
    work_type: str | None
    opus_number: str | None
    catalogue: str | None
    musical_key: str | None
    number: int | None
    mention_count: int
    aliases: list[str]


class WorkPage(BaseModel):
    items: list[WorkSummary]
    total: int
    page: int
    limit: int


class MentionOut(BaseModel):
    id: int
    source: str
    composer: str | None
    title: str
    status: str  # unmatched | auto_matched | needs_review | created | manual_matched
    score: float | None
    method: str | None
    work_id: uuid.UUID | None
    work_title: str | None
    candidate_work_id: uuid.UUID | None
    candidate_title: str | None


class MentionPage(BaseModel):
    items: list[MentionOut]
    total: int
    page: int
    limit: int


class ConcertOut(BaseModel):
    id: int
    source: str
    date: str | None
    venue: str | None
    season: str | None
    url: str | None
    role: str  # how the person participated (conductor, ...)
    works: list[str]


class ConcertPage(BaseModel):
    person_id: uuid.UUID
    person_label: str
    items: list[ConcertOut]
    total: int
    page: int
    limit: int


class ConcertSummary(BaseModel):
    id: int
    source: str
    date: str | None
    venue: str | None
    season: str | None
    event_type: str | None
    url: str | None
    conductors: list[str]
    soloist_count: int
    work_count: int


class ConcertListPage(BaseModel):
    items: list[ConcertSummary]
    total: int
    page: int
    limit: int


class ConcertParticipantOut(BaseModel):
    role: str
    name: str
    discipline: str | None
    entity_id: uuid.UUID | None


class ConcertWorkOut(BaseModel):
    title: str
    composer: str | None


class ConcertDetail(BaseModel):
    id: int
    source: str
    date: str | None
    venue: str | None
    season: str | None
    event_type: str | None
    url: str | None
    participants: list[ConcertParticipantOut]
    works: list[ConcertWorkOut]
