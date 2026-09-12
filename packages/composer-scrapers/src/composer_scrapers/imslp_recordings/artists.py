"""The people and ensembles the credit lines name, accumulated over a sweep.

Unlike :mod:`composer_scrapers.decca`, there is no artist id to key on here —
IMSLP passes the credits through as the label wrote them, a single string per
album. So the folded name has to serve as the identity, which has one
consequence worth being explicit about: two spellings of the same performer load
as two entities, and reconciling them is ``dedupe-persons``' job rather than
this module's.

What the roster is for is the *other* half — a performer credited on forty
albums must load once, carrying every instrument they were credited under, and
must not decide their profession until the last of those credits has gone past.
Hence a roster at all, and hence the adapter yielding people only after the
recordings.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import quote

from composer_schema import ENSEMBLE_KIND, PERSON_KIND, resolve_entity_kind

from .recordings import ROLE_CONDUCTOR, ROLE_ENSEMBLE, Credit


@dataclass
class Person:
    """One credited name, accumulated across every album that named them."""

    name: str
    kind: str = PERSON_KIND
    disciplines: set[str] = field(default_factory=set)
    conducted: bool = False
    albums: int = 0

    @property
    def profession(self) -> str | None:
        """What to claim this artist is, or None for an ensemble.

        Conducting wins over playing: someone credited as a conductor on one
        album and a pianist on another is a conductor who also plays, and the
        ``performs_as`` disciplines still record the playing. An ensemble has no
        profession to claim — letting one through as a person would put an
        orchestra into the person dedupe pass.
        """
        if self.kind == ENSEMBLE_KIND:
            return None
        return "conductor" if self.conducted else "soloist"

    @property
    def performs_as(self) -> list[str]:
        """The instruments and voices this artist was credited under, sorted."""
        return sorted(self.disciplines)

    @property
    def external_id(self) -> str:
        """The source-local id this artist loads under.

        The credited name, percent-encoded so it stays one token. It has to be
        the name itself rather than a hash of it: ``hash`` is salted per
        process, and an id that changed between runs would load as a new entity
        every sweep.
        """
        return f"/names/{quote(self.name, safe='')}"


class Roster:
    """Every name a sweep has credited, merged case-insensitively."""

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}

    def __len__(self) -> int:
        return len(self._people)

    def __iter__(self) -> Iterator[Person]:
        return iter(self._people.values())

    def add(self, credit: Credit) -> None:
        """Record one credit, creating the artist or extending what is known.

        A credit filed under the ensemble role settles the kind outright, and
        beats the name heuristic that would otherwise decide it: IMSLP saying
        "Fantazia Szalonzenekar" is a group is evidence, where the absence of an
        English word for "orchestra" in the name is not.
        """
        key = credit.name.casefold()
        person = self._people.get(key)
        if person is None:
            person = Person(name=credit.name, kind=resolve_entity_kind(PERSON_KIND, credit.name))
            self._people[key] = person
        if credit.role == ROLE_ENSEMBLE:
            person.kind = ENSEMBLE_KIND
        person.albums += 1
        person.conducted = person.conducted or credit.role == ROLE_CONDUCTOR
        person.disciplines.update(credit.disciplines)
