"""New York Philharmonic performance history (Kaggle dataset nyphil/perf-history).

The Philharmonic publishes its complete program archive — dataset version 3
holds 13,954 programs from the 1842-43 season through 2016-17. People occur
only as names on a program's works (``composerName``, ``conductorName``, and
per-work soloists with an English instrument/voice label); there is no
per-person id or page. Records therefore aggregate one entry per (role,
name): the profession claim, the soloist's instruments as ``performs_as``
claims (verbatim English terms, the counterpart of concertgebouw's Dutch
ones), and literal ``program_count`` / ``first_season`` / ``last_season``
claims summarizing the person's appearances — the program count is a useful
how-often-performed signal for the golden index.

Name fields need light parsing: whitespace runs collapse ("Beethoven,
Ludwig  van"), ``conductorName`` joins multiple conductors with ";" (rarely
``soloistName`` too, for dance troupes), and the "Not conducted" sentinel
marks works performed without a conductor rather than naming one. Placeholder
composers ("Anonymous,", "Traditional,") are kept verbatim; deciding they are
not people is curation and happens downstream.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import kagglehub

from . import SourceClaim, SourceRecord

NAME = "nyphil"
BASE_URL = "https://www.kaggle.com/datasets/nyphil/perf-history"

DATASET = "nyphil/perf-history"
RAW_FILE = "raw_nyc_phil.json"

ROLES = ("composer", "conductor", "soloist")

log = logging.getLogger(__name__)

# conductorName sentinel for works performed without a conductor
_NOT_CONDUCTED = "not conducted"

_WS = re.compile(r"\s+")


def _names(value: str | None) -> Iterator[str]:
    """Person names in a composerName/conductorName/soloistName value:
    ";"-separated, whitespace runs collapsed, empties and the "Not conducted"
    sentinel dropped."""
    for part in (value or "").split(";"):
        name = _WS.sub(" ", part).strip()
        if name and name.lower() != _NOT_CONDUCTED:
            yield name


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
        claims.append(SourceClaim("performs_as", "discipline", instrument))
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


def fetch_records(max_pages: int | None = None) -> Iterator[SourceRecord]:
    """Yield every composer, conductor, and soloist in the performance
    history. The whole source is one (kagglehub-cached) download; ``max_pages``
    is accepted for interface compatibility and ignored."""
    path = Path(kagglehub.dataset_download(DATASET)) / RAW_FILE
    with open(path, encoding="utf-8") as handle:
        programs: list[dict[str, Any]] = json.load(handle)["programs"]
    log.info("nyphil: %d programs", len(programs))
    people = _aggregate(programs)
    for role in ROLES:
        names = sorted(name for r, name in people if r == role)
        log.info("nyphil %s: %d records", role, len(names))
        for name in names:
            yield _record(role, name, people[(role, name)])
