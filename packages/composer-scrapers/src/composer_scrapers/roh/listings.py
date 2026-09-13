"""The two child listings a record page hangs beneath its own details.

A work page lists its productions; a production page lists its performances; and
a work page *also* lists performances directly, under the heading "Performances
not linked to a production". Both listings are ``<table class="results …">``
with positional columns and a link in the first cell.

**The third listing is the one worth knowing about.** The hierarchy reads as
work → production → performance, and a walk that takes it literally is wrong:
Aida's two 1988 concert excerpts hang off the work with no production between,
and a sweep that only descends through productions drops them without leaving a
gap anywhere it would be noticed. The count the production rows carry — "(34
performances online)" — is the cross-check that catches the other direction: it
is what the site believes it holds, so a production whose page yields a
different number of rows is a parser fault, not a quiet shortfall.

The counts are parsed rather than skipped for exactly that reason, and the
singular is real: a production with one performance says "(1 performance
online)".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import row_html, rows, table
from .urls import page_ref

_LINK_RE = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']?([^"'\s>]+)["']?""", re.IGNORECASE)
_COUNT_RE = re.compile(r"\s*\((\d+)\s+performances?\s+online\)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ProductionRef:
    """One staging, as its work page lists it."""

    production_id: int
    title: str
    companies: str
    #: How many performances the site says this production has online, or None
    #: if the cell did not carry a count. A cross-check, not a budget.
    performances: int | None


@dataclass(frozen=True)
class PerformanceRef:
    """One night, as its work or production page lists it.

    The date, time of day and venue come from *this* listing rather than from
    the performance page itself, where they are welded into the ``<h1>`` behind
    a hyphen — "Aida - Act IV scene 1 'L'abborrita rivale a me sfuggia'-28 June
    1988 Evening" — with nothing to say which hyphen separates the title from
    the date.
    """

    performance_id: int
    date: str
    time: str
    venue: str


def productions(page_body: str) -> tuple[ProductionRef, ...]:
    """Every production a work page lists, in the site's order (oldest first)."""
    fragment = table(page_body, "results", "production")
    if fragment is None:
        return ()
    found: list[ProductionRef] = []
    for record_id, cells in _linked_rows(fragment, "production"):
        detail = cells[1] if len(cells) > 1 else ""
        match = _COUNT_RE.search(detail)
        found.append(
            ProductionRef(
                production_id=record_id,
                title=cells[0],
                companies=_COUNT_RE.sub("", detail).strip(),
                performances=int(match.group(1)) if match else None,
            )
        )
    return tuple(found)


def performances(page_body: str) -> tuple[PerformanceRef, ...]:
    """Every performance a work or production page lists, in date order."""
    fragment = table(page_body, "results", "performance")
    if fragment is None:
        return ()
    return tuple(
        PerformanceRef(
            performance_id=record_id,
            date=cells[0],
            time=cells[1] if len(cells) > 1 else "",
            venue=cells[2] if len(cells) > 2 else "",
        )
        for record_id, cells in _linked_rows(fragment, "performance")
    )


def _linked_rows(fragment: str, kind: str) -> list[tuple[int, list[str]]]:
    """``(id, cells)`` for each row of *fragment* whose first cell links to a *kind*.

    The header row has no link and drops out here, as does the occasional row
    whose title is plain text because the record it names is not online.
    """
    found: list[tuple[int, list[str]]] = []
    for raw, columns in zip(row_html(fragment), rows(fragment), strict=True):
        link = _LINK_RE.search(raw)
        if link is None or not columns:
            continue
        ref = page_ref(link.group(1))
        if ref is None or ref[0] != kind:
            continue
        found.append((ref[1], columns))
    return found
