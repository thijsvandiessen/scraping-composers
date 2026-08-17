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


class ExtractedArtist(BaseModel):
    """A performer credited on a recording, with their role and (for soloists)
    instrument/voice when stated."""

    name: str
    role: str | None = Field(
        default=None,
        description="Role on the recording: 'conductor', 'soloist', or 'ensemble'; null if unclear.",
    )
    discipline: str | None = Field(
        default=None,
        description="Instrument or voice for a soloist, e.g. 'piano', 'soprano'; null if unknown.",
    )


class ExtractedRecording(BaseModel):
    """A single recording/album release: its title, publishing details, who
    performs on it, and the works it contains."""

    title: str = Field(description="Album/recording title as written on the page.")
    release_date: str | None = Field(
        default=None, description="Release date as ISO-8601 (YYYY-MM-DD) when derivable; null if unknown."
    )
    label: str | None = Field(
        default=None, description="Record label, e.g. 'Deutsche Grammophon'; null if unknown."
    )
    catalogue_number: str | None = Field(
        default=None, description="Label catalogue number of the release; null if unknown."
    )
    format: str | None = Field(
        default=None, description="Format, e.g. 'CD', 'Vinyl', 'Digital'; null if unknown."
    )
    artists: list[ExtractedArtist] = Field(default_factory=list)
    works: list[ExtractedWork] = Field(default_factory=list)


class PageRecordingExtraction(BaseModel):
    """Every recording/album found on one page (empty when the page has none)."""

    recordings: list[ExtractedRecording] = Field(default_factory=list)


class ExtractedFact(BaseModel):
    """One statement a page makes about something, as a subject/predicate/object
    triple.

    Deliberately open where the other two models are closed: the predicate is a
    free string, so a page can state something no existing scraper models. What
    comes back is folded onto a vocabulary by :mod:`.predicates` before it becomes
    a claim.
    """

    subject: str = Field(description="Who or what the statement is about, named as the page names it.")
    subject_kind: str = Field(
        default="person",
        description="What the subject is: 'person', 'work', 'ensemble', 'place', or 'publisher'.",
    )
    predicate: str = Field(description="The relationship, as a lowercase snake_case verb phrase.")
    value: str | None = Field(
        default=None, description="The stated value when it is a literal (a date, a number, a text)."
    )
    object_kind: str | None = Field(
        default=None,
        description="When the object is itself a named thing, what it is: 'work', 'person', "
        "'place', 'profession', 'genre', 'ensemble', 'publisher', 'instrumentation'; null when "
        "the object is a literal.",
    )
    object_label: str | None = Field(
        default=None, description="The named object, when object_kind is set; null otherwise."
    )


class PageClaimExtraction(BaseModel):
    """Every fact stated on one page (empty when the page states none)."""

    facts: list[ExtractedFact] = Field(default_factory=list)
