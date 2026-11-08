"""Opera houses, concert halls and orchestras: the ``t/``, ``s/`` and ``o/`` pages.

All three share a layout. The tag line reads ``"Concert hall · Vienna, Austria"``,
a ``dl.datos-sala`` holds free-form facts whose labels vary from page to page
("Opened" and "Opening", "Architect" and "Original architect"), and a
``p.mas-info`` closes the page with a link to the Wikipedia article, which is what
ties the page to a Wikidata item.

Place names are as the page spells them, which is not always English ("Roma",
"Río de Janeiro"), so only the city becomes a claim and the country stays in
``raw``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .people import text_of, wikipedia_title

_H1 = re.compile(r"<h1>(.*?)</h1>", re.S)
_TAG_LINE = re.compile(r'class="periodo-tag tipo-tag">(.*?)</p>', re.S)
_FACTS = re.compile(r'<dl class="datos-sala">(.*?)</dl>', re.S)
_FACT = re.compile(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", re.S)
_READING = re.compile(r'<p class="mas-info">.*?</p>', re.S)
_WIKI_LINK = re.compile(r'https?://([a-z-]+)\.wikipedia\.org/wiki/([^"#?]+)')
_YEAR = re.compile(r"\b(\d{4})\b")
_TAG_SEPARATOR = " · "

_OPENED_LABELS = ("Opened", "Opening")
_FOUNDED_LABELS = ("Founded",)


@dataclass(frozen=True)
class Profile:
    """A venue or orchestra page."""

    section: str
    slug: str
    name: str
    city: str | None
    country: str | None
    facts: tuple[tuple[str, str], ...]
    wikipedia: tuple[str, str] | None

    def _year(self, labels: tuple[str, ...]) -> str | None:
        for label, value in self.facts:
            if label in labels and (year := _YEAR.search(value)) is not None:
                return year.group(1)
        return None

    @property
    def opened(self) -> str | None:
        return self._year(_OPENED_LABELS)

    @property
    def founded(self) -> str | None:
        return self._year(_FOUNDED_LABELS)


def _location(tag_line: str) -> tuple[str | None, str | None]:
    """City and country from the tag line. "Singapore" has no comma: a city only."""
    _, _, where = tag_line.partition(_TAG_SEPARATOR)
    city, _, country = where.rpartition(", ")
    if not city:
        return (country or None), None
    return city, country or None


def parse_profile(section: str, slug: str, page: str) -> Profile | None:
    """The page's facts, or ``None`` if it names nothing."""
    h1 = _H1.search(page)
    name = text_of(h1.group(1)) if h1 else ""
    if not name:
        return None
    tag_line = _TAG_LINE.search(page)
    city, country = _location(text_of(tag_line.group(1))) if tag_line else (None, None)
    block = _FACTS.search(page)
    facts = (
        tuple((text_of(label), text_of(value)) for label, value in _FACT.findall(block.group(1)))
        if block
        else ()
    )
    reading = _READING.search(page)
    link = _WIKI_LINK.search(reading.group(0)) if reading else None
    return Profile(
        section=section,
        slug=slug,
        name=name,
        city=city,
        country=country,
        facts=facts,
        wikipedia=wikipedia_title(link.group(1), link.group(2)) if link else None,
    )
