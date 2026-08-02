"""Parse one Boosey & Hawkes work detail page.

The page presents work metadata as label/value pairs ("Scoring", "Duration",
"Year Composed", ...). Rather than bind to the markup that carries them — which
differs between the classic catalogue pages and the newer ones, and is the first
thing a redesign changes — the page is flattened to text lines first and the
pairs are read off by *label*. That works whether a label sits in a ``<dt>``, an
``<h3>`` or a bolded run of text, and an unrecognised label is skipped rather
than breaking the parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Tags that end a line of running text; everything else is inline and dropped.
_BLOCK = re.compile(
    r"</?(?:br|p|div|li|ul|ol|tr|td|th|h[1-6]|dt|dd|dl|table|section|article|header|footer)\b[^>]*>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TITLE_TAG = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_COMPOSER_LINK = re.compile(
    r"""<a\b[^>]*href=["'](?:https?://(?:www\.)?boosey\.com)?/composer/[^"']*["'][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)

#: Canonical field -> the labels Boosey uses for it. Matching is on the whole
#: label (not a substring), so "Abbreviated Scoring" cannot be swallowed by
#: "Scoring". Add aliases here as new ones turn up rather than adding regexes.
_LABELS: dict[str, tuple[str, ...]] = {
    "composer": ("composer", "composer/arranger"),
    "scoring": ("scoring", "instrumentation"),
    "abbreviated_scoring": ("abbreviated scoring", "abbreviated instrumentation"),
    "duration": ("duration", "playing time"),
    "year": ("year composed", "year of composition", "composed", "composition year", "date of composition"),
    "publisher": ("publisher", "imprint"),
    "movements": ("movements", "contents"),
    "translations": ("translation", "translations", "translator"),
    "text_author": ("text writer", "librettist", "author of text", "text by"),
    "dedication": ("dedication", "dedicated to"),
    "premiere": ("world premiere", "first performance", "premiere"),
    "territory": ("territory", "availability"),
}

_LABEL_INDEX: dict[str, str] = {
    label: field_name for field_name, labels in _LABELS.items() for label in labels
}

# " - Boosey & Hawkes", " | Boosey & Hawkes" and friends trailing the <title>.
_TITLE_SUFFIX = re.compile(r"\s*[|–—-]\s*boosey\s*(?:&(?:amp;)?|and)\s*hawkes.*$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedWork:
    """One work as its detail page states it. ``fields`` holds every recognised
    label/value pair verbatim; nothing is dropped or reformatted."""

    title: str
    composer: str | None
    fields: dict[str, str] = field(default_factory=dict)


def text_lines(html: str) -> list[str]:
    """Flatten a page to non-empty text lines, block tags becoming breaks."""
    html = _SCRIPT_STYLE.sub(" ", html)
    html = _COMMENT.sub(" ", html)
    html = _BLOCK.sub("\n", html)
    html = _TAG.sub(" ", html)
    lines = []
    for raw in unescape(html).splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def _labelled_values(lines: list[str]) -> dict[str, str]:
    """Read recognised label/value pairs off the flattened page.

    A value sits either after a colon on the label's own line ("Duration: 12'")
    or on the following line (the ``<dt>``/``<dd>`` and heading layouts). First
    occurrence wins: the same label can reappear in a footer or a related-works
    block further down the page.
    """
    found: dict[str, str] = {}
    for index, line in enumerate(lines):
        head, sep, tail = line.partition(":")
        label = (head if sep else line).strip().lower()
        field_name = _LABEL_INDEX.get(label)
        if field_name is None or field_name in found:
            continue
        value = tail.strip()
        if not value and index + 1 < len(lines):
            candidate = lines[index + 1].strip()
            # A label immediately followed by another label has no value.
            if candidate.rstrip(":").lower() not in _LABEL_INDEX:
                value = candidate
        if value:
            found[field_name] = value
    return found


def _title(html: str, lines: list[str]) -> str:
    if match := _H1.search(html):
        title = re.sub(r"\s+", " ", unescape(_TAG.sub(" ", match.group(1)))).strip()
        if title:
            return title
    if match := _TITLE_TAG.search(html):
        title = re.sub(r"\s+", " ", unescape(_TAG.sub(" ", match.group(1)))).strip()
        title = _TITLE_SUFFIX.sub("", title).strip()
        if title:
            return title
    return lines[0] if lines else ""


def _composer(html: str, fields: dict[str, str]) -> str | None:
    """The composer, from the page's ``/composer/...`` link.

    Boosey links the composer's name on every work page, which is a far more
    reliable signal than the URL slug (where composer and title run together
    with no separator that can be split on).
    """
    for match in _COMPOSER_LINK.finditer(html):
        name = re.sub(r"\s+", " ", unescape(_TAG.sub(" ", match.group(1)))).strip()
        if name and name.lower() not in {"composers", "all composers", "browse composers"}:
            return name
    return fields.get("composer")


def duration_minutes(raw: str | None) -> int | None:
    """Whole minutes from Boosey's free-text duration, or ``None``.

    Handles ``12'``, ``c. 12 minutes``, ``12-14 mins`` (lower bound — the shorter
    reading is the one a programmer can rely on fitting) and ``1 hour 5 minutes``.
    """
    if not raw:
        return None
    text = raw.strip().lower()
    hours = 0
    if match := re.search(r"(\d+)\s*(?:h\b|hr\b|hrs\b|hour)", text):
        hours = int(match.group(1))
        text = text[match.end() :]
    match = re.search(r"\d+", text)
    minutes = int(match.group()) if match else 0
    return (hours * 60 + minutes) or None


def parse_work(html: str) -> ParsedWork | None:
    """Parse a work detail page, or ``None`` when it carries no usable title."""
    lines = text_lines(html)
    title = _title(html, lines)
    if not title:
        return None
    fields = _labelled_values(lines)
    return ParsedWork(title=title, composer=_composer(html, fields), fields=fields)
