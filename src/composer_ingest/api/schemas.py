import uuid
from datetime import datetime

from pydantic import BaseModel


class ComposerSummary(BaseModel):
    id: uuid.UUID
    label: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ClaimOut(BaseModel):
    predicate: str
    value: str | None
    object_label: str | None
    source: str
    source_url: str | None


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
