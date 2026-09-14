"""The production page — one staging of a work, and the nights it ran.

``production.aspx?production=<id>`` is the same detail table as the other two
levels (:mod:`.credits`) over a listing of its performances (:mod:`.listings`).
It names nobody musical: the credits here are direction, design and staging, and
the composer stays on the work page above it.

**The page does not say which work it belongs to.** There is no link upward and
no work id anywhere in the markup — the only edge between the two levels is the
productions listing on the work page, pointing down. So a production is only
ever read as a child of the work that named it, and :func:`parse` takes that
work id from the caller rather than looking for one. A production reached any
other way would be a staging of nothing.

**Its title carries the year, and is the staging's, not the work's.** "Aida
(1994)" is the Moshinsky production; the work is "Aida". Nothing here should be
paired with a composer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .credits import Credit, read, unknown_labels
from .listings import PerformanceRef, performances
from .text import body, table, text

#: Labels on a production page that credit a person. An allowlist, as on the
#: work page and for the same reason (see :mod:`.credits`): the tempting
#: inversion — treat every unrecognised row as a credit — files "Opera in four
#: acts" as a human being the first time a new field appears. The qualifier rule
#: in :mod:`.credits` is what keeps this list short: "Revival associate
#: director", "Assistant director" and "Director" are one entry.
#:
#: This tier has a genuine long tail — a hundred sampled stagings produced a
#: dozen labels used exactly once ("Cloth designer", "Incidental dances",
#: "Artistic Collaborator") — and it does not converge, because some rows are
#: not a vocabulary at all: two co-production partners are filed under labels
#: that are the partner's own name ("Theater an der Wien, Vienna"). So the
#: adapter's report of unknown labels is expected to be non-empty on a full
#: sweep, and is how this list grows; what the allowlist guarantees is that the
#: tail costs a credit rather than inventing a person out of a fact.
ROLES = frozenset(
    {
        "artistic_collaborator",
        "choreographer",
        "cloth_designer",
        "combat_sequences_director",
        # "Additional music: Ian Page (recitatives)" — a staging can commission a
        # composer the work page never names, so this level credits one too.
        "composer",
        "costume_designer",
        "costumes_realized",
        "design_associate",
        "design_concept",
        "design_realisation",
        "director",
        "dramaturg",
        "fight_director",
        "incidental_dances",
        "lighting_design_recreated",
        "lighting_designer",
        "lighting_execution",
        "lighting_realisation",
        "lighting_recreated",
        "movement_director",
        "orchestrator",
        "producer",
        "production_realization",
        "rehearsal_and_staging",
        "rehearsed",
        "set_designer",
        "sets_recreated",
        "stage_conception",
        "staging",
        "video_designer",
    }
)

#: Labels on a production page that state a fact rather than credit a person.
#: "Co-production with" and "Hire from" always name an opera house rather than a
#: person, and are the reason this level cannot simply treat every institution
#: as an ensemble and move on.
FIELDS = frozenset(
    {
        "co_production_with",
        "company",
        "electronic_music_production",
        "hire_from",
        "notes",
        "production_premiere",
        "roh_premiere",
        "world_premiere",
    }
)

#: Rows naming people that are not a credit this source should record. "Sponsor"
#: is a semicolon-separated list of donors — "Anonymous donors; Simon Robertson;
#: Virginia Robertson; …" — which is neither one person nor an artistic credit,
#: and parsing it as a name would file a fundraising list as a cast member.
NOT_CREDITS = frozenset({"sponsor", "supported"})

_HEADING_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2\s*>", re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.DOTALL | re.IGNORECASE)
_DETAILS_RE = re.compile(r"^(.*?):\s*production details$", re.IGNORECASE)


@dataclass(frozen=True)
class ProductionPage:
    """One staging, as its own page states it."""

    production_id: int
    #: The work this production stages, from the listing that led here — see the
    #: module docstring on why it cannot come from the page.
    work_id: int
    title: str
    genre: str | None
    credits: tuple[Credit, ...] = ()
    fields: dict[str, str] = field(default_factory=dict)
    performances: tuple[PerformanceRef, ...] = ()
    unknown: frozenset[str] = frozenset()

    @property
    def company(self) -> str | None:
        return self.fields.get("Company")

    @property
    def premiere(self) -> str | None:
        return self.fields.get("Production premiere")


def parse(production_id: int, work_id: int, page: str) -> ProductionPage:
    """Read ``production.aspx?production=<production_id>`` as a child of *work_id*."""
    panel = body(page)
    detail = table(panel, "result", "work") or ""
    credits, fields = read(detail, ROLES)
    return ProductionPage(
        production_id=production_id,
        work_id=work_id,
        title=_title(panel),
        genre=_genre(panel),
        credits=credits,
        fields=fields,
        performances=performances(panel),
        unknown=frozenset(unknown_labels(detail, ROLES, FIELDS | NOT_CREDITS)),
    )


def _title(panel: str) -> str:
    match = _TITLE_RE.search(panel)
    return text(match.group(1)) if match else ""


def _genre(panel: str) -> str | None:
    for heading in _HEADING_RE.findall(panel):
        match = _DETAILS_RE.match(text(heading))
        if match is not None:
            return match.group(1).strip()
    return None
