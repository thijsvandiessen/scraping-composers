"""The people and ensembles the catalogue credits, from two places at once.

Most of them arrive free with the recordings: every track names its
contributors, so a sweep of the catalogue has already seen several thousand
artists by the time it finishes, each with a stable id and the functions they
were credited under. :class:`Roster` collects those as they go past.

A much smaller set — 80 artists — also has a page of its own on the site, and
those add a biography and the catalogue's "Lastname, Firstname" sort form.
Worth folding in, with one caveat that shapes the code below: **the roster
carries no composers**. ``isComposer`` is ``false`` for all 80 of them, because
the flag marks artists the label promotes, not artists who compose. Composers
are identified from the track credits (see :mod:`.products`) and nowhere else,
so a roster entry never decides a profession — it only enriches a record whose
profession the credits already settled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from composer_schema import resolve_entity_kind

from .products import Credit
from .urls import artist_url

#: Functions that say *what someone is* rather than what they played. Everything
#: else a performer is credited under is an instrument or a voice, and becomes a
#: ``performs_as`` discipline.
_NOT_A_DISCIPLINE = frozenset({"artist", "conductor", "composer", "performer", ""})


@dataclass
class Person:
    """One artist, accumulated across every track that credited them."""

    key: str
    name: str
    artist_id: int | None = None
    kind: str = "person"
    functions: set[str] = field(default_factory=set)
    url_alias: str | None = None
    flagged_composer: bool = False
    composer: bool = False
    sort_name: str | None = None
    bio: str | None = None
    theme_type: str | None = None
    born: str | None = None
    died: str | None = None

    @property
    def profession(self) -> str | None:
        """What to claim this artist is, or None for an ensemble.

        Composer wins over performing roles: someone credited as a composer on
        one track and a pianist on another is a composer who also played, and
        the ``performs_as`` disciplines still record the playing. An ensemble
        has no profession to claim — letting one through as a person would put
        an orchestra into the person dedupe pass.
        """
        if self.kind == "ensemble":
            return None
        if self.composer:
            return "composer"
        if any(function.casefold() == "conductor" for function in self.functions):
            return "conductor"
        return "soloist"

    @property
    def disciplines(self) -> list[str]:
        """The instruments and voices this artist was credited under, sorted."""
        return sorted(
            {
                function
                for function in self.functions
                if function.casefold() not in _NOT_A_DISCIPLINE and "composer" not in function.casefold()
            }
        )

    @property
    def url(self) -> str | None:
        """The artist's own page, when the site publishes one for them."""
        return artist_url(self.url_alias) if self.url_alias else None

    @property
    def external_id(self) -> str:
        """The source-local id this artist is loaded under.

        Stable across runs, which the ``(source_id, external_id)`` uniqueness in
        silver depends on: the catalogue's own artist id where there is one, and
        the credited name where the only mention was a display heading.
        """
        if self.artist_id is not None:
            return f"/artists/{self.artist_id}"
        return f"/names/{quote(self.name, safe='')}"


class Roster:
    """Every artist a sweep has seen, merged by the catalogue's artist id.

    Keyed by id rather than name on purpose: the catalogue spells the same
    person differently across releases, and the id is what makes "Plácido
    Domingo" and "Placido Domingo" one record instead of two.
    """

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}

    def __len__(self) -> int:
        return len(self._people)

    def __iter__(self) -> Any:
        return iter(self._people.values())

    def add(self, credit: Credit, *, composer: bool = False) -> None:
        """Record one credit, creating the artist or extending what is known."""
        if credit.artist_id <= 0:
            self.add_named(credit.name, composer=composer)
            return
        key = f"id:{credit.artist_id}"
        person = self._people.get(key)
        if person is None:
            person = Person(
                key=key,
                name=credit.name,
                artist_id=credit.artist_id,
                kind=credit.kind,
                url_alias=credit.url_alias,
                flagged_composer=credit.flagged_composer,
            )
            self._people[key] = person
        if credit.function:
            person.functions.add(credit.function)
        person.url_alias = person.url_alias or credit.url_alias
        person.flagged_composer = person.flagged_composer or credit.flagged_composer
        person.composer = person.composer or composer

    def add_named(self, name: str, *, composer: bool = False) -> None:
        """Record an artist the catalogue named only in a display heading.

        A heading is a string, not a relation, so there is no id to key on and
        the folded name has to serve. It is the name itself rather than a hash
        of it: ``hash`` is salted per process, and an id that changes between
        runs would load as a new entity every sweep.
        """
        key = f"name:{name.casefold()}"
        person = self._people.get(key)
        if person is None:
            self._people[key] = Person(
                key=key, name=name, kind=resolve_entity_kind("person", name), composer=composer
            )
            return
        person.composer = person.composer or composer

    def reconcile(self) -> int:
        """Fold heading-only artists into the credited artist of the same name.

        A composer can reach the roster two ways — credited with an id on one
        track, named only in a display heading on another — and those must not
        load as two entities. The id-keyed record wins because it carries the
        catalogue's own identity; the name-keyed one contributes whatever it
        knew that the other did not. Returns how many were folded away.

        Matching on the folded name is safe here in a way it would not be across
        sources: both spellings came from the same catalogue, about the same
        track listing.
        """
        by_name = {
            person.name.casefold(): person for person in self._people.values() if person.artist_id is not None
        }
        folded = 0
        for key, person in list(self._people.items()):
            if person.artist_id is not None:
                continue
            target = by_name.get(person.name.casefold())
            if target is None:
                continue
            target.functions |= person.functions
            target.composer = target.composer or person.composer
            target.born = target.born or person.born
            target.died = target.died or person.died
            del self._people[key]
            folded += 1
        return folded

    def life_dates(self, dates: dict[str, tuple[str | None, str | None]]) -> None:
        """Attach birth and death years recovered from headings, by name.

        Matched on name because that is all a heading gives; a person already
        carrying dates keeps them, so the first statement wins rather than the
        last.
        """
        if not dates:
            return
        by_name = {person.name.casefold(): person for person in self._people.values()}
        for name, (born, died) in dates.items():
            person = by_name.get(name.casefold())
            if person is None:
                continue
            person.born = person.born or born
            person.died = person.died or died

    def enrich(self, node: dict[str, Any]) -> None:
        """Fold one roster page into the artist the credits already produced.

        An artist with a page but no credit in this sweep is added outright —
        capped runs read a slice of the catalogue but the whole roster, and the
        page is evidence they exist either way.
        """
        artist_id = node.get("idRaw")
        if not isinstance(artist_id, int):
            return
        name = _text(node.get("screenname")) or _text(node.get("name"))
        if name is None:
            return
        key = f"id:{artist_id}"
        person = self._people.get(key)
        if person is None:
            person = Person(
                key=key,
                name=name,
                artist_id=artist_id,
                kind=resolve_entity_kind("person", name),
            )
            self._people[key] = person
        person.sort_name = _text(node.get("name"))
        person.bio = _text(node.get("seoDescription"))
        person.theme_type = _text(node.get("themeType"))
        person.url_alias = person.url_alias or _text(node.get("urlAlias"))
        # Deliberately not `person.composer |= isComposer`: see the module
        # docstring. The flag marks promotion, not composition.
        person.flagged_composer = person.flagged_composer or bool(node.get("isComposer"))


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
