"""Per-person aggregation: one record per (role, name) across all programs.

People occur only as names on a program's works; there is no per-person id.
Each record carries the profession claim, the soloist's instruments as
``performs_as`` claims (verbatim English terms), and literal ``program_count``
/ ``first_season`` / ``last_season`` claims summarizing the appearances — the
program count is a useful how-often-performed signal for the golden index.
Placeholder composers ("Anonymous,", "Traditional,") are kept verbatim;
deciding they are not people is curation and happens downstream.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .. import SourceClaim, SourceRecord
from .text import _WS, _names

ROLES = ("composer", "conductor", "soloist")


@dataclass
class _Person:
    """Aggregate of one (role, name) across all programs."""

    programs: set[str] = field(default_factory=set)
    seasons: set[str] = field(default_factory=set)
    instruments: set[str] = field(default_factory=set)


def _aggregate(programs: Iterable[dict[str, Any]]) -> dict[tuple[str, str], _Person]:
    people: dict[tuple[str, str], _Person] = {}

    def seen(role: str, name: str, program_id: str, season: str) -> _Person:
        person = people.setdefault((role, name), _Person())
        person.programs.add(program_id)
        person.seasons.add(season)
        return person

    for program in programs:
        program_id = str(program["programID"])
        season = program["season"]
        for work in program["works"]:
            for name in _names(work.get("composerName")):
                seen("composer", name, program_id, season)
            for name in _names(work.get("conductorName")):
                seen("conductor", name, program_id, season)
            for soloist in work.get("soloists", ()):
                instrument = _WS.sub(" ", soloist.get("soloistInstrument") or "").strip()
                for name in _names(soloist.get("soloistName")):
                    person = seen("soloist", name, program_id, season)
                    if instrument:
                        person.instruments.add(instrument)
    return people


def _record(role: str, name: str, person: _Person) -> SourceRecord:
    claims = [SourceClaim("has_profession", "profession", role)]
    for instrument in sorted(person.instruments):
        claims.append(SourceClaim("performs_as", value=instrument))
    # seasons are uniformly "YYYY-YY", so lexical min/max are first and last
    first, last = min(person.seasons), max(person.seasons)
    claims.append(SourceClaim("program_count", value=str(len(person.programs))))
    claims.append(SourceClaim("first_season", value=first))
    claims.append(SourceClaim("last_season", value=last))
    raw: dict[str, Any] = {
        "role": role,
        "name": name,
        "program_count": len(person.programs),
        "first_season": first,
        "last_season": last,
    }
    if person.instruments:
        raw["instruments"] = sorted(person.instruments)
    return SourceRecord(
        external_id=f"{role}:{name}",
        name=name,
        url=None,
        raw=raw,
        claims=tuple(claims),
    )
