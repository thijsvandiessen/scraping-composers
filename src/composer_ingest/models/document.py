from datetime import datetime, timezone
from typing import Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar('T')

class Document(BaseModel, Generic[T]):
    """
    A unified representation of an ingested document across all sources.
    """
    id: str = Field(..., description="Stable identifier for the document")
    url: str = Field(..., description="Canonical or reference URL")
    source_name: str = Field(..., description="Name of the source the document came from")
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), 
        description="Timestamp of ingestion"
    )
    body: T = Field(..., description="Source-specific document content payload")
