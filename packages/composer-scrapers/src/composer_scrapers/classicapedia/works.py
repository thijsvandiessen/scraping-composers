"""The works a biography page lists, ready for the work matcher.

A page has up to two lists. ``#catalogo`` is the full catalogue, by genre, and
``#obras`` the notable ones; the catalogue wins where both exist, since the notable
list only repeats part of it. ``#obras`` alone also holds summaries that are not
works at all ("Approximately 78 original works for guitar").

**Keys cannot be trusted.** Titles end in a one-letter key, and accidentals are
lost: Beethoven's Eroica reads "Symphony no.3 Eroica, E" and his Fifth Piano
Concerto "Piano Concerto no.5 Emperor, E", both of them E flat. So the token is cut
from the title the matcher sees, and survives only verbatim in ``raw``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .people import text_of

_CATALOGUE = re.compile(r'<section id="catalogo".*?</section>', re.S)
_NOTABLE = re.compile(r'<h2 id="obras".*?(?=<section\b|</article>)', re.S)
_GROUP = re.compile(r'(?:<summary>(.*?)</summary>|<h3>(.*?)</h3>)?\s*<ul class="obras">(.*?)</ul>', re.S)
_ITEM = re.compile(r"<li>(.*?)</li>", re.S)
_YEARS_SPAN = re.compile(r'<span class="anyo">\s*\(?(.*?)\)?\s*</span>', re.S)
_COUNT = re.compile(r"\s*\(\d+\)\s*$")

#: ", E" or ", c": a key letter between commas or at the end of the title.
_KEY_TOKEN = re.compile(r",\s*[A-Ga-g](?=\s*(?:,|$))")
#: A trailing parenthetical holding a year: "(1924)", "(ballet, 1955)", "(Paris, 1731), lost".
_TRAILING_DATE = re.compile(r"\s*\(([^()]*\b\d{4}\b[^()]*)\)(?:,\s*(?:possibly\s+)?lost)?\s*$", re.I)
#: "Approximately 78 ...", "Some 120 ...", "... (around 200)". A bare leading number
#: is not a summary: "1812 Overture", "4'33''" and "6 sonatas, vn" are all works.
_SUMMARY = re.compile(
    r"^(?:approximately|about|around|some|over|more than|nearly)\s+\d|\(\s*(?:around|about|c\.?)\s*\d+\s*\)",
    re.I,
)


@dataclass(frozen=True)
class ListedWork:
    title: str
    verbatim: str
    genre: str | None
    years: str | None

    def as_raw(self) -> dict[str, str | None]:
        return {"listed_as": self.verbatim, "genre": self.genre, "years": self.years}


def strip_key(title: str) -> str:
    """*title* without its key letter (see the module docstring)."""
    return _KEY_TOKEN.sub("", title).strip()


def _work(item: str, genre: str | None) -> ListedWork | None:
    span = _YEARS_SPAN.search(item)
    years = text_of(span.group(1)) if span else None
    verbatim = text_of(_YEARS_SPAN.sub("", item))
    if not verbatim or _SUMMARY.search(verbatim):
        return None
    date = _TRAILING_DATE.search(verbatim)
    title = verbatim[: date.start()] if date else verbatim
    if date and years is None:
        years = date.group(1).strip()
    title = strip_key(title)
    return ListedWork(title, verbatim, genre, years) if title else None


def parse_works(page: str) -> tuple[ListedWork, ...]:
    """The page's works: the catalogue if it has one, otherwise the notable list."""
    region = _CATALOGUE.search(page) or _NOTABLE.search(page)
    if region is None:
        return ()
    works: list[ListedWork] = []
    for summary, heading, items in _GROUP.findall(region.group(0)):
        genre = _COUNT.sub("", text_of(summary or heading)) or None
        works.extend(work for item in _ITEM.findall(items) if (work := _work(item, genre)) is not None)
    return tuple(works)
