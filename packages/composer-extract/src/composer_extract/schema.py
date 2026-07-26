"""The structured shape the LLM extracts from one crawled page.

These Pydantic models serve double duty: their JSON schema is handed to Ollama
as the required response ``format``, and the model validates the response back
into typed objects. Kept deliberately flat and close to a concert programme so a
local model can fill it reliably.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedSoloist(BaseModel):
    """A soloist performing at a concert, with their instrument/voice if stated."""

    name: str
    discipline: str | None = Field(
        default=None, description="Instrument or voice, e.g. 'violin', 'soprano'; null if unknown."
    )


class ExtractedWork(BaseModel):
    """One work on the programme and, when stated, who composed it."""

    title: str
    composer: str | None = Field(default=None, description="Composer's name; null if not stated.")


class ExtractedConcert(BaseModel):
    """A single concert: when/where, who led/performed, and what was played."""

    date: str | None = Field(
        default=None, description="Concert date as ISO-8601 (YYYY-MM-DD); null if unknown."
    )
    venue: str | None = Field(default=None, description="Venue or hall name; null if unknown.")
    conductors: list[str] = Field(default_factory=list, description="Conductor name(s).")
    soloists: list[ExtractedSoloist] = Field(default_factory=list)
    works: list[ExtractedWork] = Field(default_factory=list)


class PageExtraction(BaseModel):
    """Every concert found on one page (empty when the page has none)."""

    concerts: list[ExtractedConcert] = Field(default_factory=list)
