"""The A-Z composer index: one ``person`` record per listed composer.

Each letter page lists its composers as ``<tr class="for_search">`` rows whose
anchor holds the name in "Surname, Given" order followed by up to two
parenthesised spans — the life dates and the country. Both are optional: three
quarters of the rows name no country at all, and a handful (e.g. "Anonymous,")
carry neither.

The dates come in several shapes — "born 1970", "1593-1625", and vaguer forms
the site uses for the poorly documented: "16??-17??", "18__-?", "XVI-XVII"
(roman century ranges, sometimes with the Russian abbreviation for "century").
Only fully numeric years are asserted as ``born_on``/``died_on``; everything
else is kept verbatim in ``raw`` rather than turned into a half-known year.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .. import SourceClaim, SourceRecord

_ROW = re.compile(r'<tr class="for_search".*?</tr>', re.S)
_LINK = re.compile(r'<a\s+href="(/en/composer/[^"]*?/(\d+))"[^>]*>(.*?)</a>', re.S)
_SPAN = re.compile(r"<span[^>]*>(.*?)</span>", re.S)
_TAG = re.compile(r"<[^>]+>")
_YEAR = re.compile(r"^\d{3,4}$")
# "XVII-XVII", "XVIв-XVIв", "I-II" — uppercase-only, so a country name (always
# title case, and rarely spelled out of roman numerals) can never match.
_CENTURY = re.compile(r"^[IVXLCDM]+\s*в?(?:\s*-\s*[IVXLCDM]+\s*в?)?$")


@dataclass(frozen=True)
class IndexEntry:
    """One composer as the index page lists them."""

    external_id: str
    url: str
    name: str
    dates: str
    country: str
    letter: str = ""


def _text(fragment: str) -> str:
    """Tag-free, unescaped, whitespace-collapsed text of an HTML fragment."""
    return html.unescape(re.sub(r"\s+", " ", _TAG.sub("", fragment))).strip()


def _span_text(fragment: str) -> str:
    return _text(fragment).strip("()").strip()


def _is_life_dates(span: str) -> bool:
    """Whether a span holds life dates rather than a country.

    Dates either start with "born", contain a digit or a "?" placeholder, or
    are a roman-numeral century range.
    """
    if span.lower().startswith("born"):
        return True
    if any(char.isdigit() or char == "?" for char in span):
        return True
    return bool(_CENTURY.match(span))


def _year(text: str) -> str | None:
    """A year, or ``None`` when the source only gives a vague one ("16??")."""
    text = text.strip()
    return text if _YEAR.match(text) else None


def parse_life_years(dates: str) -> tuple[str | None, str | None]:
    """Split the index's date span into (born, died), dropping vague years."""
    text = dates.strip()
    if text.lower().startswith("born"):
        return _year(text[len("born") :]), None
    born, separator, died = text.partition("-")
    if not separator:
        return None, None
    return _year(born), _year(died)


def iter_index_entries(page: str, base_url: str = "", letter: str = "") -> list[IndexEntry]:
    """Parse one letter page's composer rows.

    Only ``for_search`` rows count: the page opens with a "Notable Composers"
    box repeating a dozen of the same composers as bare links, which would
    otherwise be parsed a second time (without their dates or country).
    """
    entries: list[IndexEntry] = []
    for row in _ROW.findall(page):
        link = _LINK.search(row)
        if link is None:
            continue
        path, external_id, inner = link.groups()
        spans = [_span_text(span) for span in _SPAN.findall(inner)]
        name = _text(_SPAN.sub("", inner)).rstrip(",").strip()
        if not name:
            continue
        entries.append(
            IndexEntry(
                external_id=external_id,
                url=base_url + path,
                name=name,
                dates=next((span for span in spans if _is_life_dates(span)), ""),
                country=next((span for span in spans if not _is_life_dates(span)), ""),
                letter=letter,
            )
        )
    return entries


def index_record(entry: IndexEntry) -> SourceRecord:
    """Build the composer's person record from its index row."""
    claims = [SourceClaim("has_profession", "profession", "composer")]
    born, died = parse_life_years(entry.dates)
    if born:
        claims.append(SourceClaim("born_on", value=born))
    if died:
        claims.append(SourceClaim("died_on", value=died))
    if entry.country:
        claims.append(SourceClaim("citizen_of", "place", entry.country))
    return SourceRecord(
        external_id=entry.external_id,
        name=entry.name,
        url=entry.url or None,
        raw={
            "name": entry.name,
            "dates": entry.dates,
            "country": entry.country,
            "letter": entry.letter,
            "url": entry.url,
        },
        claims=tuple(claims),
    )
