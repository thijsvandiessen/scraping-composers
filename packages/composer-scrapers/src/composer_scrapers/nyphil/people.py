"""Per-person aggregation: one record per (role, name) across all programs.

People occur only as names on a program's works; there is no per-person id.
Each record carries the profession claim, the soloist's instruments as
``performs_as`` claims (verbatim English terms), and literal ``program_count``
/ ``first_season`` / ``last_season`` claims summarizing the appearances — the
program count is a useful how-often-performed signal for the golden index.
A name printed with a bracketed spelling (``Bartok [Bartók], Béla``) is
recorded under that spelling, with the ASCII form as an ``also_known_as``
claim (see :func:`~.text.spelling`).
Placeholder composers ("Anonymous,", "Traditional,") are kept verbatim;
deciding they are not people is curation and happens downstream.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .. import SourceClaim, SourceRecord
from .text import WS, spellings

ROLES = ("composer", "conductor", "soloist")


@dataclass
class _Person:
    """Aggregate of one (role, name) across all programs."""

    programs: set[str] = field(default_factory=set[str])
    seasons: set[str] = field(default_factory=set[str])
    instruments: set[str] = field(default_factory=set[str])
    aliases: set[str] = field(default_factory=set[str])


def aggregate(programs: Iterable[dict[str, Any]]) -> dict[tuple[str, str], _Person]:
    people: dict[tuple[str, str], _Person] = {}

    def seen(role: str, spelling: tuple[str, tuple[str, ...]], program_id: str, season: str) -> _Person:
        name, aliases = spelling
        person = people.setdefault((role, name), _Person())
        person.programs.add(program_id)
        person.seasons.add(season)
        person.aliases.update(aliases)
        return person

    for program in programs:
        program_id = str(program["programID"])
        season = program["season"]
        for work in program["works"]:
            for spelling in spellings(work.get("composerName")):
                seen("composer", spelling, program_id, season)
            for spelling in spellings(work.get("conductorName")):
                seen("conductor", spelling, program_id, season)
            for soloist in work.get("soloists", ()):
                instrument = WS.sub(" ", soloist.get("soloistInstrument") or "").strip()
                for spelling in spellings(soloist.get("soloistName")):
                    person = seen("soloist", spelling, program_id, season)
                    if instrument:
                        person.instruments.add(instrument)
    return people


def record(role: str, name: str, person: _Person) -> SourceRecord:
    claims = [SourceClaim("has_profession", "profession", role)]
    claims.extend(SourceClaim("performs_as", value=instrument) for instrument in sorted(person.instruments))
    claims.extend(SourceClaim("also_known_as", value=alias) for alias in sorted(person.aliases))
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
    if person.aliases:
        raw["aliases"] = sorted(person.aliases)
    return SourceRecord(
        external_id=f"{role}:{name}",
        name=name,
        url=None,
        raw=raw,
        claims=tuple(claims),
    )


_aggregate = aggregate
_record = record
