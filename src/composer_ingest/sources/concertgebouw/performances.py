"""The "List" view: one ``work`` record per work-performance.

The view is one big table (columns DATE, CITY, COMPOSER, TITLE, CONDUCTOR,
SOLOIST). A row with a DATE starts a new concert (its date/city carry forward
to the following rows); a row with a TITLE is a work; a row whose only filled
cell is SOLOIST is an extra soloist for the current work (soloists are never
joined in one cell — multi-soloist works span several rows). Each work links to
its composer, conductor, soloists, date, and city as claims, so the same person
can be a composer in one concert and a conductor/soloist in another and still be
one entity. Counting is left to the golden index; this layer stays verbatim.

Soloists may carry a voice or instrument type in parentheses, e.g.
"Oehman, Martin (tenor)". The parenthetical is stripped from the name so the
entity deduplicates correctly with the dropdown-derived record, and a separate
``person`` record is emitted carrying the ``performs_as`` discipline claim.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from .. import SourceClaim, SourceRecord

# the List-view result table and its rows/cells
_TABLE = re.compile(r'<table id="zoekresultaat".*?</table>', re.DOTALL)
_ROW = re.compile(r"<tr[^>]*>")
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# "(viool)", "(tenor)", "(mezzosopraan)", ... — same pattern as dropdowns._DISCIPLINE
_DISCIPLINE = re.compile(r"\(([^()]+)\)$")


@dataclass
class _Perf:
    """One work-performance accumulated across its (possibly several) rows."""

    index: int
    date: str
    city: str
    composer: str
    title: str
    conductor: str
    soloists: list[tuple[str, str | None]] = field(default_factory=list)  # (name, discipline)


def _cell_text(cell: str) -> str:
    return _WS.sub(" ", html.unescape(_TAGS.sub(" ", cell))).strip()


def _parse_soloist(raw: str) -> tuple[str, str | None]:
    """Split 'Name (tenor)' into ('Name', 'tenor'); plain names return (name, None)."""
    m = _DISCIPLINE.search(raw)
    if m:
        return raw[: m.start()].strip(), m.group(1).strip()
    return raw, None


def _list_rows(page: str) -> Iterator[list[str]]:
    """Yield the cleaned cell texts of each data row of the List-view table."""
    table = _TABLE.search(page)
    if table is None:
        raise ValueError("result table #zoekresultaat not found in list view; did the site change?")
    for chunk in _ROW.split(table.group(0))[1:]:
        cells = _CELL.findall(chunk)
        if len(cells) < 6:  # header row (<th>) and any stray markup
            continue
        yield [_cell_text(c) for c in cells]


def _performance_record(perf: _Perf) -> SourceRecord:
    claims: list[SourceClaim] = []
    if perf.composer:
        claims.append(SourceClaim("composed_by", "person", perf.composer))
    if perf.conductor:
        claims.append(SourceClaim("conducted_by", "person", perf.conductor))
    for name, _ in perf.soloists:
        claims.append(SourceClaim("performed_by", "person", name))
    if perf.date:
        claims.append(SourceClaim("performed_on", value=perf.date))
    if perf.city:
        claims.append(SourceClaim("performed_in", "place", perf.city))
    return SourceRecord(
        external_id=f"perf:{perf.index}",
        name=perf.title,
        url=None,
        raw={
            "idx": perf.index,
            "date": perf.date,
            "city": perf.city,
            "composer": perf.composer,
            "title": perf.title,
            "conductor": perf.conductor,
            "soloists": [name for name, _ in perf.soloists],
        },
        kind="work",
        claims=tuple(claims),
    )


def _soloist_discipline_records(perf: _Perf) -> Iterator[SourceRecord]:
    """Yield a ``person`` record for each soloist that carries a voice/discipline type."""
    for name, discipline in perf.soloists:
        if discipline:
            yield SourceRecord(
                external_id=f"soloist:{name}:{discipline}",
                name=name,
                url=None,
                raw={"name": name, "discipline": discipline},
                kind="person",
                claims=(
                    SourceClaim("has_profession", "profession", "soloist"),
                    SourceClaim("performs_as", "discipline", discipline),
                ),
            )


def _performances(page: str) -> Iterator[SourceRecord]:
    """Yield one ``work`` record per work-performance in the List view, plus a
    ``person`` record for each soloist whose name includes a voice or instrument
    type in parentheses.

    A row with a DATE opens a concert (date/city carry forward); a row with a
    TITLE is a new work; a row whose only content is a SOLOIST adds a soloist to
    the current work. ``index`` is the running 0-based work counter, matching
    the archive's own global work index."""
    current_date = current_city = ""
    perf: _Perf | None = None
    index = -1
    for date, city, composer, title, conductor, soloist in (cells[:6] for cells in _list_rows(page)):
        if date:
            current_date, current_city = date, city
        if title:
            if perf is not None:
                yield _performance_record(perf)
                yield from _soloist_discipline_records(perf)
            index += 1
            perf = _Perf(index, current_date, current_city, composer, title, conductor)
            if soloist:
                perf.soloists.append(_parse_soloist(soloist))
        elif soloist and perf is not None:
            perf.soloists.append(_parse_soloist(soloist))
    if perf is not None:
        yield _performance_record(perf)
        yield from _soloist_discipline_records(perf)
