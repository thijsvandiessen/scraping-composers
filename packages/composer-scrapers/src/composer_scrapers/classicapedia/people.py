"""What a biography page states about its subject: facts only.

The biographies themselves are the site's own writing, quotable with credit, so
no prose is kept. What is kept is the page's structured parts:

- the subtitle, ``"1770–1827 · Bonn · German composer and pianist"``, whose
  middle slot is usually the birthplace but not reliably: it can be missing, a
  second date (``"c 1650"``) or a doubt (``"Milan or Vercelli"``);
- the ``maestros`` section, teachers and pupils with the subject taught when the
  page gives one. Each list is capped on the page (at 40), so a prolific teacher's
  pupils are only complete through the pupils' own pages.

Names and the subject slot sometimes hold a dictionary-style disambiguator
("Ignace Joseph Pleyel (i)"), and a name can keep the "and" of the sentence it was
lifted from ("Anton Kraft and"). Both are removed here.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

_TAG = re.compile(r"<[^>]+>")
_H1 = re.compile(r"<h1>(.*?)</h1>", re.S)
_SUBTITLE = re.compile(r'<p class="sub">(.*?)</p>', re.S)
_SUBTITLE_SEPARATOR = " · "

_LIFESPAN = re.compile(r"^(\d{2,4})[–-](\d{2,4})$")
_BORN = re.compile(r"^b\.\s*(\d{2,4})$")
_DIED = re.compile(r"^d\.\s*(\d{2,4})$")
#: "c 1650" in the place slot: the page's birth year is an estimate.
_CIRCA_SLOT = re.compile(r"^c\.?\s*\d")
_MODERN_NAME = re.compile(r"\s*\[[^\]]*\]")
#: "family of German harpsichord makers": a page about a dynasty, not a person.
_FAMILY = re.compile(r"^(?:[\w-]+\s+){0,2}(?:family|dynasty)\b", re.I)

#: Past the oldest lifespans the site plausibly states (Denis Hempson, 112): "1846–1996"
#: is a typo for 1946, and "172–1790" for 1720.
_LONGEST_LIFE = 115

_MAESTROS = re.compile(r'<section id="maestros".*?</section>', re.S)
_RELATION_LIST = re.compile(r'<h3>(.*?)</h3>\s*<ul class="obras rel">(.*?)</ul>', re.S)
_ITEM = re.compile(r"<li>(.*?)</li>", re.S)
_LINK = re.compile(r'<a\s+href="([^"]+)\.html"[^>]*>(.*?)</a>', re.S)
_SUBJECT = re.compile(r'<span class="mat">\s*\(?(.*?)\)?\s*</span>', re.S)
_ROMAN_ONLY = re.compile(r"^[ivx]+$", re.I)
_TRAILING_AND = re.compile(r"[\s,]+(?:and|&)\s*$", re.I)
_DISAMBIGUATOR = re.compile(r"\s*\((?:i{1,3}|iv|v|vi{0,3})\)\s*$", re.I)

_TEACHERS_HEADING = "Studied with"
_PUPILS_HEADING = "Pupils"

_WIKI_SOURCE = re.compile(r'class="fuente-wiki">.*?</p>', re.S)
_WIKI_LINK = re.compile(r'https?://([a-z-]+)\.wikipedia\.org/wiki/([^"#?]+)')


@dataclass(frozen=True)
class Relation:
    """One person a teachers or pupils list names.

    ``slug`` is the linked biography; ``None`` for a name the site does not link.
    """

    name: str
    slug: str | None = None
    subject: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "slug": self.slug, "subject": self.subject}


@dataclass(frozen=True)
class Biography:
    slug: str
    name: str
    birth_year: str | None
    death_year: str | None
    dates: str
    birth_place: str | None
    occupation: str
    teachers: tuple[Relation, ...]
    pupils: tuple[Relation, ...]
    #: ``(language, title)`` of the Wikipedia article the page cites as a source.
    wikipedia: tuple[str, str] | None

    @property
    def is_family(self) -> bool:
        return _FAMILY.match(self.occupation) is not None


def text_of(fragment: str) -> str:
    """Tag-free, unescaped, whitespace-collapsed text of an HTML fragment."""
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def plain_name(name: str) -> str:
    """*name* without a trailing "(i)" or a stray "and"."""
    return _DISAMBIGUATOR.sub("", _TRAILING_AND.sub("", name)).strip()


def wikipedia_title(language: str, quoted: str) -> tuple[str, str]:
    """``(language, title)`` for a link's quoted path segment."""
    return language, unquote(quoted).replace("_", " ")


def _life_years(dates: str) -> tuple[str | None, str | None]:
    """Birth and death year, for the shapes that state one. "fl. c. 1500" (the
    period someone was active) states neither.

    A span that cannot be a life states neither either, because there is no telling
    which half is wrong: "1904–1904" (Dallapiccola died in 1975) and "1625–1625" (Caignet
    died in 1625) err on opposite ends, and "384–322" is BC, which the field cannot
    express. The page's own string stays in ``raw``.
    """
    if (span := _LIFESPAN.match(dates)) is not None:
        born, died = span.group(1), span.group(2)
        return (born, died) if 0 < int(died) - int(born) <= _LONGEST_LIFE else (None, None)
    if (born := _BORN.match(dates)) is not None:
        return born.group(1), None
    if (died := _DIED.match(dates)) is not None:
        return None, died.group(1)
    return None, None


def _birth_place(slot: str) -> str | None:
    """The middle slot, when it can only be a single place."""
    place = _MODERN_NAME.sub("", slot).strip()
    if not place or not place[0].isupper() or " or " in place or any(char.isdigit() for char in place):
        return None
    return place


def _subject(item: str) -> str | None:
    match = _SUBJECT.search(item)
    if match is None:
        return None
    subject = text_of(match.group(1))
    return None if not subject or _ROMAN_ONLY.match(subject) else subject


def _relation(item: str) -> Relation | None:
    subject = _subject(item)
    link = _LINK.search(item)
    if link is not None:
        name = plain_name(text_of(link.group(2)))
        return Relation(name, link.group(1).rsplit("/", 1)[-1], subject) if name else None
    name = plain_name(text_of(_SUBJECT.sub("", item)))
    return Relation(name, None, subject) if name else None


def _relations(page: str) -> tuple[tuple[Relation, ...], tuple[Relation, ...]]:
    """The page's teachers and pupils. Cut to the section first, so that a link
    elsewhere on the page cannot read as a teacher."""
    section = _MAESTROS.search(page)
    if section is None:
        return (), ()
    found: dict[str, list[Relation]] = {_TEACHERS_HEADING: [], _PUPILS_HEADING: []}
    for heading, items in _RELATION_LIST.findall(section.group(0)):
        if (bucket := found.get(text_of(heading))) is not None:
            bucket.extend(rel for item in _ITEM.findall(items) if (rel := _relation(item)) is not None)
    return tuple(found[_TEACHERS_HEADING]), tuple(found[_PUPILS_HEADING])


def _wikipedia(page: str) -> tuple[str, str] | None:
    """The article the page cites, or ``None`` unless it cites exactly one.

    The citation belongs to the page's curiosities, not to its subject, so a page
    naming two articles says nothing about which is the person. Whether the one
    it names really is them is checked against Wikidata (see ``resolve``).
    """
    cited = {
        wikipedia_title(language, title)
        for block in _WIKI_SOURCE.findall(page)
        for language, title in _WIKI_LINK.findall(block)
    }
    return cited.pop() if len(cited) == 1 else None


def parse_biography(slug: str, page: str) -> Biography | None:
    """The page's facts, or ``None`` if it names nobody."""
    h1 = _H1.search(page)
    name = plain_name(text_of(h1.group(1))) if h1 else ""
    if not name:
        return None
    subtitle = _SUBTITLE.search(page)
    parts = text_of(subtitle.group(1)).split(_SUBTITLE_SEPARATOR) if subtitle else []
    dates = parts[0] if parts else ""
    birth_year, death_year = _life_years(dates)
    birth_place: str | None = None
    if len(parts) >= 3:  # noqa: PLR2004 - dates, place, occupation
        if _CIRCA_SLOT.match(parts[1]):
            birth_year = None
        birth_place = _birth_place(parts[1])
    teachers, pupils = _relations(page)
    return Biography(
        slug=slug,
        name=name,
        birth_year=birth_year,
        death_year=death_year,
        dates=dates,
        birth_place=birth_place,
        occupation=parts[-1] if len(parts) >= 2 else "",  # noqa: PLR2004
        teachers=teachers,
        pupils=pupils,
        wikipedia=_wikipedia(page),
    )
