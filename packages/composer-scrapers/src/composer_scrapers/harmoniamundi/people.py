"""The people the label credits, from the album pages and their own profiles.

Most arrive free with the releases: every album names its performers and its
composers, each with the role they were credited under and — for the ones the
label gives a page to — a link to it. :class:`Roster` accumulates those as the
sweep goes past, which is why the people can only be emitted after every album
has been read: a profession is settled by the whole set of credits a person
appears under, not by the first one seen.

The profile pages add what a credit cannot say. An artist page states the
instrument or voice the label files them under; a composer page states their
life dates. Both are folded in by :meth:`Roster.enrich`.

Two decisions worth naming. **A composer page and an artist page are separate
records**, even for the same person: they are two pages, each its own evidence,
and silver keys entities on the normalized name — so the two records merge into
one entity there and contribute their claims to it, which is the wanted result
without this tier having to guess that they are the same. And **the
discography on a profile page is not read**: it renders client-side, so the
static HTML carries roughly one of an artist's albums. Album-to-person
relations come from the album pages, which are complete.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import quote

from composer_schema import resolve_entity_kind
from composer_schema.kinds import ENSEMBLE_KIND, PERSON_KIND

from .albums import Credit
from .contents import fold
from .text import text
from .urls import detail_path, detail_url

#: Roles that say what someone *is* rather than what they played.
_NOT_A_DISCIPLINE = frozenset(
    {"", "artist", "artists", "composer", "composers", "conductor", "performer", "orchestra"}
)

_TITLE_RE = re.compile(r'<h1 class="humain_title">(.*?)</h1>', re.S)
_HERO_RE = re.compile(r'<div class="hero_content as_h4">(.*?)</div>', re.S)
_BIO_RE = re.compile(r'<div id="biographie"[^>]*>\s*<div class="global_content">(.*?)</div>', re.S)

#: A composer page states life dates where an artist page states an instrument:
#: ``1668 - 1733``, and ``1959 -`` for a living composer.
_HERO_DATES_RE = re.compile(r"^(?:[a-zA-Z]{1,2}\.\s*)?(\d{3,4})?\s*[-–—]\s*(\d{3,4})?$")

#: A hero line can name more than one discipline (``Soprano, Harpsichord``).
_DISCIPLINE_SPLIT_RE = re.compile(r"[,/]")

_BIOGRAPHY_HEADING_RE = re.compile(r"^Biography\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Profile:
    """One artist or composer page."""

    kind: str  # "artist" | "composer"
    slug: str
    name: str
    hero: str | None = None
    born: str | None = None
    died: str | None = None
    bio: str | None = None

    @property
    def url(self) -> str:
        return detail_url(self.kind, self.slug)

    @property
    def disciplines(self) -> list[str]:
        """The instruments or voices the hero line names, if it names any.

        Empty on a composer page, whose hero line is life dates.
        """
        if self.hero is None or self.born or self.died:
            return []
        found = [part.strip() for part in _DISCIPLINE_SPLIT_RE.split(self.hero)]
        return [part for part in found if part and part.casefold() not in _NOT_A_DISCIPLINE]


def parse_profile(page_html: str, kind: str, page_slug: str) -> Profile | None:
    """The person *page_html* describes, or None when it describes none."""
    title = _TITLE_RE.search(page_html)
    if title is None:
        return None
    name = text(title.group(1))
    if not name:
        return None
    hero_match = _HERO_RE.search(page_html)
    hero = text(hero_match.group(1)) or None if hero_match else None
    born, died = _life_dates(hero)
    bio_match = _BIO_RE.search(page_html)
    bio = _BIOGRAPHY_HEADING_RE.sub("", text(bio_match.group(1))) or None if bio_match else None
    return Profile(kind=kind, slug=page_slug, name=name, hero=hero, born=born, died=died, bio=bio)


def _life_dates(hero: str | None) -> tuple[str | None, str | None]:
    """The life dates a composer page's hero line states, if it states any."""
    if not hero:
        return None, None
    match = _HERO_DATES_RE.match(hero)
    if match is None or not (match.group(1) or match.group(2)):
        return None, None
    return match.group(1), match.group(2)


@dataclass
class Person:
    """One person or ensemble, accumulated across every credit and page."""

    key: str
    name: str
    ref: tuple[str, str] | None = None
    kind: str = "person"
    roles: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)
    born: str | None = None
    died: str | None = None
    hero: str | None = None
    bio: str | None = None
    albums: int = 0

    @property
    def profession(self) -> str | None:
        """What to claim this person is, or None for an ensemble.

        Composer wins over performing roles: someone filed under Composers on
        one release and playing the harpsichord on another is a composer who
        also played, and the ``performs_as`` disciplines still record the
        playing. An ensemble has no profession to claim — letting one through as
        a person would put an orchestra into the person dedupe pass.
        """
        if self.kind == "ensemble":
            return None
        if "composers" in self.columns:
            return "composer"
        if any(role.casefold() == "conductor" for role in self.roles):
            return "conductor"
        return "soloist"

    @property
    def disciplines(self) -> list[str]:
        """The instruments and voices this person was credited under, sorted."""
        return sorted({role for role in self.roles if role.casefold() not in _NOT_A_DISCIPLINE})

    @property
    def url(self) -> str | None:
        """This person's own page, when the label publishes one for them."""
        return detail_url(self.ref[0], self.ref[1]) if self.ref else None

    @property
    def external_id(self) -> str:
        """The source-local id this person is loaded under.

        Stable across runs, which the ``(source_id, external_id)`` uniqueness in
        silver depends on: the page's own path where the label publishes one, and
        the credited name where it does not. The name itself and not a hash of
        it — ``hash`` is salted per process, so an id built from one would load a
        new entity every sweep.
        """
        if self.ref is not None:
            return detail_path(*self.ref)
        return f"/names/{quote(self.name, safe='')}"


class Roster:
    """Every person a sweep has seen, merged by the page the label links them to.

    Keyed by profile slug where there is one, because that is the label's own
    identity for them and it survives the spelling drifting between releases.
    Everyone else is keyed by folded name, which is all a bare credit gives.
    """

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}

    def __len__(self) -> int:
        return len(self._people)

    def __iter__(self) -> Iterator[Person]:
        return iter(self._people.values())

    def add(self, credit: Credit) -> None:
        """Record one album credit, creating the person or extending them."""
        key = f"{credit.ref[0]}:{credit.ref[1]}" if credit.ref else f"name:{fold(credit.name)}"
        person = self._people.get(key)
        if person is None:
            person = Person(
                key=key,
                name=credit.name,
                ref=credit.ref,
                kind=ENSEMBLE_KIND if credit.is_ensemble else PERSON_KIND,
            )
            self._people[key] = person
        if credit.role:
            person.roles.add(credit.role)
        person.columns.add(credit.column)
        person.albums += 1

    def enrich(self, profile: Profile) -> None:
        """Fold one profile page into the person the credits already produced.

        A person with a page but no credit in this sweep is added outright —
        a capped run reads a slice of the catalogue but the whole profile set,
        and the page is evidence they exist either way.
        """
        key = f"{profile.kind}:{profile.slug}"
        person = self._people.get(key)
        if person is None:
            person = Person(
                key=key,
                name=profile.name,
                ref=(profile.kind, profile.slug),
                kind=resolve_entity_kind("person", profile.name),
            )
            self._people[key] = person
        if profile.kind == "composer":
            person.columns.add("composers")
        person.roles.update(profile.disciplines)
        person.born = person.born or profile.born
        person.died = person.died or profile.died
        person.hero = person.hero or profile.hero
        person.bio = person.bio or profile.bio

    def life_dates(self, dates: dict[str, tuple[str | None, str | None]]) -> None:
        """Attach life dates recovered from tracklist headings, by name.

        Matched on the folded name because a heading gives nothing else. A
        person already carrying dates keeps them, so the first statement wins
        rather than the last.
        """
        if not dates:
            return
        by_name = {fold(person.name): person for person in self._people.values()}
        for name, (born, died) in dates.items():
            person = by_name.get(fold(name))
            if person is None:
                continue
            person.born = person.born or born
            person.died = person.died or died

    def reconcile(self) -> int:
        """Fold bare-name people into the linked person of the same name.

        A person reaches the roster two ways — linked to their page on one
        album, named without a link on another — and those must not load as two
        entities. The linked record wins because it carries the label's own
        identity; the bare one contributes whatever it knew that the other did
        not. Returns how many were folded away.

        Matching on the folded name is safe here in a way it would not be across
        sources: both spellings came from the same label, about the same
        releases.
        """
        linked = {fold(p.name): p for p in self._people.values() if p.ref is not None}
        folded = 0
        for key, person in list(self._people.items()):
            if person.ref is not None:
                continue
            target = linked.get(fold(person.name))
            if target is None:
                continue
            target.roles |= person.roles
            target.columns |= person.columns
            target.albums += person.albums
            target.born = target.born or person.born
            target.died = target.died or person.died
            del self._people[key]
            folded += 1
        return folded
