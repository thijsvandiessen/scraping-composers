"""Everyone the archive names, gathered across the sweep.

The database has no person pages. A name is plain text in a table cell, repeated
in full every time it is credited — "Marius Petipa" appears on dozens of ballet
works, "Plácido Domingo" on hundreds of nights — with no id, no link and no
disambiguation. So the name *is* the key here, and this registry is where the
same name credited in twenty places becomes one record instead of twenty.

**A profession is claimed only where the label states one.** Being listed under
"Composer" on a work page makes someone a composer; being listed under
"Conductor" on a performance makes them a conductor. Being listed in the cast
does not make them anything, and that is deliberate: the cast column holds the
*part*, not the discipline — "Don José", "Tavern Dancer", "Harpsichord
continuo", "Pas de huit" — and a 1947 Carmen credits opera singers and ballet
dancers in the same table. There is no signal that separates them, so no
profession is claimed and the parts are recorded as what they are. The
alternative, guessing "singer" from the genre, is precisely the sort of
invention that made the earlier LLM crawls of this repository's sources
worthless.

**Ensembles are typed as ensembles.** The cast list ends with the orchestra and
the chorus — "The Covent Garden Orchestra", "Sadler's Wells Ballet",
"Orchestra of the Age of Enlightenment" — in the same rows as the singers.
:func:`~composer_schema.resolve_entity_kind` catches them, which keeps a chorus
out of the person dedupe pass downstream.

**Parts are capped, counts are not.** A busy performer accumulates thousands of
cast rows over a fifty-year career and the raw record must not become a
transcript of them, so the parts list is truncated at :data:`_MAX_PARTS` while
the credit counts stay exact.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from composer_schema import ENSEMBLE_KIND, SourceClaim, resolve_entity_kind

from .. import SourceRecord
from .credits import PROFESSIONS

#: How many distinct parts a person's raw record keeps. Beyond this the count
#: still rises but the list stops growing — see the module docstring.
_MAX_PARTS = 50


@dataclass
class Person:
    """One name, and everything the archive credited it with."""

    name: str
    #: Printed label -> how many times it credited them, e.g. {"Composer": 41}.
    labels: Counter[str] = field(default_factory=Counter)
    #: Slugged roles, for the profession claims.
    roles: set[str] = field(default_factory=set)
    #: Cast parts, capped. Ordered on the way out for a stable record.
    parts: set[str] = field(default_factory=set)
    #: Genres they were credited in, e.g. {"Opera", "Ballet"}.
    genres: set[str] = field(default_factory=set)
    works: set[int] = field(default_factory=set)
    productions: set[int] = field(default_factory=set)
    performances: set[int] = field(default_factory=set)
    first_date: str | None = None
    last_date: str | None = None

    @property
    def professions(self) -> tuple[str, ...]:
        """The professions this person's credits support, in a stable order."""
        return tuple(sorted({p for role in self.roles if (p := PROFESSIONS.get(role))}))


class Registry:
    """The people seen so far, keyed by name.

    Names are taken verbatim: no case folding, no accent stripping, no initials
    expanded. The archive is internally consistent about how it spells someone
    — it is one catalogue with one house style — and the entity resolution that
    reconciles "Modest Petrovich Musorgsky" here with "Modest Mussorgsky"
    elsewhere is the warehouse's job, done against every source at once, not
    this adapter's guess made against one.
    """

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}

    def __len__(self) -> int:
        return len(self._people)

    def credit(
        self,
        name: str,
        role: str,
        label: str,
        *,
        genre: str | None = None,
        part: str | None = None,
    ) -> Person | None:
        """Record one credit, returning the person it landed on.

        None for an empty name — the cast table's act markers and un-cast parts
        come through here and must not create a person with no name.
        """
        if not name.strip():
            return None
        person = self._people.setdefault(name, Person(name=name))
        person.labels[label] += 1
        person.roles.add(role)
        if genre:
            person.genres.add(genre)
        if part and len(person.parts) < _MAX_PARTS:
            person.parts.add(part)
        return person

    def seen_at(self, person: Person | None, iso: str | None) -> None:
        """Widen a person's date range to include *iso*, if it is a date at all."""
        if person is None or not iso:
            return
        if person.first_date is None or iso < person.first_date:
            person.first_date = iso
        if person.last_date is None or iso > person.last_date:
            person.last_date = iso

    def records(self) -> list[SourceRecord]:
        """One record per name, ordered by name so a run is reproducible."""
        return [_record(person) for _, person in sorted(self._people.items())]


def _record(person: Person) -> SourceRecord:
    """A person or ensemble, with the claims their credits support and no more."""
    kind = resolve_entity_kind("person", person.name)
    raw = {
        "credited_as": dict(sorted(person.labels.items())),
        "genres": sorted(person.genres),
        "parts": sorted(person.parts),
        "works": len(person.works),
        "productions": len(person.productions),
        "performances": len(person.performances),
        "first_performance": person.first_date,
        "last_performance": person.last_date,
    }
    if kind == ENSEMBLE_KIND:
        # An orchestra has no profession to claim, and letting one through as a
        # person puts it into the person dedupe pass.
        return SourceRecord(
            external_id=f"person:{person.name}", name=person.name, url=None, raw=raw, kind=kind
        )
    claims = tuple(
        SourceClaim("has_profession", "profession", profession) for profession in person.professions
    )
    return SourceRecord(
        external_id=f"person:{person.name}",
        name=person.name,
        url=None,
        raw={**raw, "professions": list(person.professions)},
        kind=kind,
        claims=claims,
    )
