"""Per-performance ``work`` records: one per titled work at each concert.

A program is often played on several dates, so each titled work yields one
record per concert (``interval``/intermission entries are skipped). Each links
the work to its composer(s)/conductor(s)/soloist(s) and the concert's date and
location as claims — the same shape the concertgebouw List view produces, so
people stay cross-role and "most played" is a query over these raw records.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from ...document import Document, work_mention_document
from .text import _WS, _names


def _date(value: str | None) -> str:
    """The date part of an ISO concert timestamp ("1842-12-07T05:00:00Z")."""
    return (value or "").split("T", 1)[0]


def _title(value: object) -> str:
    """A work's title. Usually a string, but a handful are dicts split around
    an emphasized fragment (``{"em": "PRINCE IGOR", "_": "CHORUS FROM ..."}``);
    those are joined back into one verbatim string."""
    if isinstance(value, dict):
        text = " ".join(str(part) for part in value.values())
    elif isinstance(value, str):
        text = value
    else:
        text = ""
    return _WS.sub(" ", text).strip()


def _performance_record(
    program_id: str,
    season: str,
    concert_idx: int,
    work_idx: int,
    concert: dict[str, Any],
    work: dict[str, Any],
) -> Document:
    title = _title(work.get("workTitle"))
    composers = list(_names(work.get("composerName")))
    conductors = list(_names(work.get("conductorName")))
    soloists = [
        {"name": name, "discipline": (s.get("soloistInstrument") or None)}
        for s in work.get("soloists", ())
        for name in _names(s.get("soloistName"))
    ]
    date = _date(concert.get("Date"))
    location = _WS.sub(" ", concert.get("Location") or "").strip()

    raw: dict[str, Any] = {
        "programID": program_id,
        "season": season,
        "date": date,
        "venue": concert.get("Venue"),
        "location": location,
        "eventType": concert.get("eventType"),
        "title": title,
        "composers": composers,
        "conductors": conductors,
        "soloists": soloists,
    }
    if work.get("movement"):
        raw["movement"] = work["movement"]
    return work_mention_document(
        id=f"perf:{program_id}:{concert_idx}:{work_idx}",
        title=title,
        composer=composers[0] if composers else None,
        raw=raw,
    )


def _performances(programs: Iterable[dict[str, Any]]) -> Iterator[Document]:
    """Yield one work mention per titled work at each concert of its program.

    ``work_idx`` is the work's position in the program's ``works`` list (kept
    stable by counting intervals too); intermission/untitled entries are
    skipped."""
    for program in programs:
        program_id = str(program["programID"])
        season = program["season"]
        for concert_idx, concert in enumerate(program.get("concerts", ())):
            for work_idx, work in enumerate(program["works"]):
                if not _title(work.get("workTitle")):
                    continue
                yield _performance_record(program_id, season, concert_idx, work_idx, concert, work)
