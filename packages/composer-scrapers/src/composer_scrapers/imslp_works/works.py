"""Parse one IMSLP work detail page.

The page states work metadata as a clean ``<tr><th>Label</th><td>Value</td></tr>``
infobox — "Composer", "Opus/Catalogue Number", "Key", "Year/Date of
Composition", "Genre Categories", and (the field the user actually asked
for) "Instrumentation". Some labels wrap a long and short form in
``<span class="mh555">``/``<span class="ms555">`` (e.g. "Opus/Catalogue
Number" / "Op./Cat. No."); the short form is dropped before reading the
label text.

A later "Sheet Music" table on the same page reuses some ``<th>`` labels
("Publisher Info.", "Copyright", ...) per uploaded score/recording, but never
the infobox's own labels — so, as in ``boosey/works.py``, reading every
``<th>``/``<td>`` pair on the page with first-occurrence-wins is enough; no
separate table boundary needs to be found.

The title is passed through exactly as the page's ``<title>`` states it
(with composer suffix stripped, since the composer is already known from
which category page this work was found on) — nothing is ever appended to
it. One composer's catalogue routinely shares opus numbers across distinct
works ("11 Bagatelles, Op.119" vs "6 Bagatelles, Op.126"), and the work
matcher (``composer_warehouse.works.match``) treats a matching parsed opus as
near-proof of identity, so folding extra text into the title risks the same
false-merge trap ``boosey/works.py`` and ``classicalmusiconline/works.py``
document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

_TH_TD = re.compile(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_MS555_SPAN = re.compile(r'<span class="ms555">.*?</span>', re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_TITLE_TAG = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

#: Infobox label -> canonical field name. Matching is on the whole label (not
#: a substring), read straight off the page rather than guessed at.
_LABELS: dict[str, str] = {
    "Composer": "composer",
    "Opus/Catalogue Number": "opus_catalogue_number",
    "Internal Reference Number": "internal_reference_number",
    "Key": "key",
    "Movements/Sections": "movements",
    "Year/Date of Composition": "composition_year",
    "First Publication": "first_publication",
    "Dedication": "dedication",
    "Composer Time Period": "composer_time_period",
    "Piece Style": "piece_style",
    "Genre Categories": "genre_categories",
    "Instrumentation": "instrumentation",
}

# " - IMSLP" trailing the <title>.
_TITLE_SUFFIX = re.compile(r"\s*-\s*imslp\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedWork:
    """One work as its detail page states it. ``fields`` holds every
    recognised label/value pair verbatim; nothing is dropped or reformatted."""

    title: str
    fields: dict[str, str] = field(default_factory=dict)


def _clean(html_fragment: str) -> str:
    """Tag-stripped, unescaped, whitespace-collapsed text of an HTML fragment."""
    text = _TAG.sub(" ", html_fragment)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _labelled_values(html: str) -> dict[str, str]:
    """Every recognised ``<th>``/``<td>`` pair on the page, first wins."""
    found: dict[str, str] = {}
    for th_html, td_html in _TH_TD.findall(html):
        label = _clean(_MS555_SPAN.sub("", th_html))
        field_name = _LABELS.get(label)
        if field_name is None or field_name in found:
            continue
        value = _clean(td_html)
        if value:
            found[field_name] = value
    return found


def _title(html: str) -> str:
    if match := _TITLE_TAG.search(html):
        title = _clean(match.group(1))
        return _TITLE_SUFFIX.sub("", title).strip()
    return ""


def strip_composer_suffix(title: str, composer_label: str) -> str:
    """The page title with the trailing composer disambiguation removed.

    IMSLP titles every work page "Title (Composer)"; the composer is already
    known from which category page this work was found on, so this is a
    plain string strip, not a parse.
    """
    suffix = f" ({composer_label})"
    if title.endswith(suffix):
        return title[: -len(suffix)]
    return title


def parse_work(html: str) -> ParsedWork | None:
    """Parse a work detail page, or ``None`` when it carries no usable title."""
    title = _title(html)
    if not title:
        return None
    return ParsedWork(title=title, fields=_labelled_values(html))
