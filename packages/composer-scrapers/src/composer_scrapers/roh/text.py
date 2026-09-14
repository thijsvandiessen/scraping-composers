"""Reading the one markup shape every rohcollections.org.uk record page is built from.

All three detail levels — work, production, performance — render as a
``<th>label</th><td>value</td>`` table, so one reader serves all three and the
per-level modules only decide what the labels mean. Four things about that
markup need saying once here rather than at every call site.

**The markup is hand-mixed ASP.NET, and its case and quoting are not
consistent.** The same page emits ``<TABLE class="result work" border=0>`` from
one template and ``<TR><TH>`` from another next to ``<tr><th>``; the search
listing writes ``class=results`` with no quotes at all. Every pattern here is
case-insensitive and accepts bare, single- and double-quoted attributes, and
classes are compared as a token set rather than as a string, because
``class="result work"`` must match a request for ``result`` and for ``work``.

**Labels carry a colon, sometimes on its own line.** Fixed rows are written
``<TH>Company:</TH>``, while the repeated credit rows are generated and come out
as ``<th>\\n Conductor\\n</th>`` — no colon — or, on the relay rows,
``<th>\\n Television - future relay\\n :\\n</th>``, with the colon on a line of
its own. :func:`label` normalises all three to ``Television - future relay``, so
a lookup table can be written the way a reader sees the page.

**A credit's qualifier hangs off a ``<br>``, not an element.** Design and
revival credits read ``<td>Mikhail Shishliannikov <br />Credited in the
programme for the Royal Opera House performances of August 2011</td>`` and
``<td>David Edwards <br />(1995 revival)</td>``. The name is what precedes the
first break; everything after it is a note about the credit. Splitting on the
break is the only rule that holds — the note is free text, and for the
Mariinsky set it is a sentence that would otherwise be read as part of a
person's name.

**Tags are dropped, not spaced.** Titles here are marked up inline —
``Boris Godunov [1896]``, ``Symphony No. 3 'Symphony of Sorrowful Songs'`` — and
replacing each tag with a space would put one before every comma and bracket.
Anything line-level is turned into a newline by :func:`lines` before the strip
runs, so removing the rest outright reproduces the rendered string.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

#: ``<table …>`` with its attributes, up to its closing tag. Non-greedy, which is
#: safe because the record tables on these pages are never nested — the cast
#: table sits beside the detail table in a sibling ``<div>``, not inside it.
_TABLE_RE = re.compile(r"<table\b([^>]*)>(.*?)</table\s*>", re.DOTALL | re.IGNORECASE)
_CLASS_RE = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)

#: One ``<th>``/``<td>`` pair. The two cells need not be in the same ``<tr>`` —
#: they always are, but the row tags are written inconsistently enough (``<TR>``,
#: ``<tr class="odd">``, and one template that omits the close) that requiring a
#: well-formed row costs rows for nothing.
_PAIR_RE = re.compile(r"<th\b[^>]*>(.*?)</th\s*>\s*<td\b[^>]*>(.*?)</td\s*>", re.DOTALL | re.IGNORECASE)

#: A ``<td>`` cell, for the listing tables whose columns are positional.
_CELL_RE = re.compile(r"<td\b[^>]*>(.*?)</td\s*>", re.DOTALL | re.IGNORECASE)
_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)(?=<tr\b|</table|\Z)", re.DOTALL | re.IGNORECASE)


def text(fragment: str) -> str:
    """*fragment* with its tags removed, entities resolved and whitespace collapsed.

    Non-breaking spaces are folded into ordinary ones — the templates use them
    for layout inside titles and between a date and its time of day — so a
    caller comparing strings is not surprised by one.
    """
    stripped = html.unescape(_TAG_RE.sub("", _COMMENT_RE.sub("", fragment)))
    return _WS_RE.sub(" ", stripped.replace("\xa0", " ")).strip()


def lines(fragment: str) -> list[str]:
    """*fragment* as the non-empty lines it renders as, split on ``<br>``."""
    return [line for line in (text(part) for part in _BREAK_RE.split(fragment)) if line]


def label(fragment: str) -> str:
    """A ``<th>``'s text as a lookup key — trimmed, de-colonned, collapsed.

    See the module docstring: the colon may be absent, attached, or sitting on
    its own line, depending on which template wrote the row.
    """
    return text(fragment).rstrip(":").strip()


def body(page: str) -> str:
    """The record panel of *page*, with scripts and styles removed.

    Every record page wraps its content in ``#ContentPlaceHolderBody_uiPanelShowData``
    and follows it with a phone sidebar that repeats the "Browse by Title" links
    and then the site footer. Narrowing to the panel keeps the footer's own
    tables and the header navigation out of every pattern below it.
    """
    clean = _SCRIPT_RE.sub("", page)
    start = clean.find("uiPanelShowData")
    if start < 0:
        return clean
    end = clean.find('id="sidebarwrapperphone"', start)
    return clean[start : end if end > 0 else len(clean)]


def classes(attributes: str) -> frozenset[str]:
    """The class tokens in a tag's attribute string."""
    match = _CLASS_RE.search(attributes)
    if match is None:
        return frozenset()
    value = match.group(1) or match.group(2) or match.group(3) or ""
    return frozenset(value.split())


def tables(fragment: str, *required: str, without: str | None = None) -> Iterator[str]:
    """The inner HTML of each ``<table>`` in *fragment* carrying every class in *required*.

    Called with no classes it yields every table, in document order. *without*
    excludes a class, which matters because the class sets on these pages nest:
    a performance page carries ``class="result"`` (company, venue, conductor)
    and ``class="result work"`` (language, durations, information source) as two
    different tables, and a request for ``result`` matches both.
    """
    wanted = frozenset(required)
    for match in _TABLE_RE.finditer(fragment):
        found = classes(match.group(1))
        if wanted <= found and (without is None or without not in found):
            yield match.group(2)


def table(fragment: str, *required: str, without: str | None = None) -> str | None:
    """The first table in *fragment* matching *required*, or None if there is none.

    None is a real answer here, not an error: a cross-reference stub's work page
    carries the detail table's header and nothing in it, and a performance with
    no cast has no cast table at all.
    """
    return next(tables(fragment, *required, without=without), None)


def pairs(fragment: str) -> list[tuple[str, str]]:
    """Every ``(label, value_html)`` pair in *fragment*, in document order.

    The value keeps its markup so a caller can split a credit from its note
    (:func:`credit`) or read the ``<i>`` a cast row hides a character
    description in. Duplicate labels are preserved and ordered, because that is
    how the site says a work has five choreographers.
    """
    return [(label(k), v) for k, v in _PAIR_RE.findall(fragment)]


def row_html(fragment: str) -> list[str]:
    """Each ``<tr>`` of *fragment*, with its markup, in document order.

    The one place rows are split, because two callers walk the same table in
    parallel — the listings read a row's link from the markup and its columns
    from :func:`rows` — and a second copy of this pattern that drifted would
    silently misalign a link with the wrong row.

    A row is terminated by the next ``<tr>``, the closing ``</table>``, *or the
    end of the fragment*: :func:`tables` hands back a table's inner HTML with
    the closing tag already removed, so requiring one drops the last row of
    every listing on the site.
    """
    return _ROW_RE.findall(fragment)


def cells(row: str) -> list[str]:
    """The ``<td>`` cells of one row, with their markup."""
    return _CELL_RE.findall(row)


def rows(fragment: str) -> list[list[str]]:
    """Every ``<tr>``'s ``<td>`` cells as rendered text, for the positional listings.

    Header-only rows come back as an empty list rather than being dropped, so a
    caller can tell "no rows" from "a row I could not read".
    """
    return [[text(cell) for cell in cells(row)] for row in row_html(fragment)]


def credit(value: str) -> tuple[str, str | None]:
    """A credit cell as ``(name, note)`` — see the module docstring on the ``<br>``.

    The note is None when the cell is a bare name, which is the common case;
    when the cell is empty the name is empty and the caller drops the credit.
    """
    parts = lines(value)
    if not parts:
        return "", None
    return parts[0], " ".join(parts[1:]) or None
