"""One ``person`` (or ``ensemble``) record per distinct artist.

Every concert embeds its artists with a stable id and ``role`` in both the
concert-level ``_links.artist`` (orchestra, conductor) and each work's
``_links.artist`` (composer, soloist, arranger). The same artist recurs across
concerts and roles, so we accumulate them by id over the whole archive and emit
one record each: professions from the artist's ``fields_of_work`` and the roles
they were seen in, and (for soloists) the instrument/voice they played as
``performs_as`` disciplines. Groups (orchestras, ensembles) become ``ensemble``
records, keyed by the same id, with no professions.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .. import SourceClaim, SourceRecord
from .fetch import BASE_URL

# role.type -> the profession asserted by appearing in that role
_ROLE_PROFESSION = {
    "composer": "composer",
    "conductor": "conductor",
    "instrument": "soloist",
    "voice": "soloist",
    "arrangement": "arranger",
}
_SOLOIST_ROLES = frozenset({"instrument", "voice"})


@dataclass
class _Artist:
    """An artist accumulated across the concerts they appear in."""

    id: str
    name: str
    is_group: bool
    professions: set[str] = field(default_factory=set)
    disciplines: set[str] = field(default_factory=set)


def _register(registry: dict[str, _Artist], artist: dict[str, Any]) -> None:
    artist_id = artist.get("id")
    name = artist.get("group_name") if artist.get("display_type") == "group" else artist.get("name")
    if not artist_id or not name:
        return
    info = registry.get(artist_id)
    if info is None:
        info = _Artist(id=artist_id, name=name, is_group=artist.get("display_type") == "group")
        registry[artist_id] = info

    role: dict[str, Any] = artist.get("role") or {}
    role_type, role_name = role.get("type"), role.get("name")
    for field_of_work in artist.get("fields_of_work") or []:
        info.professions.add(field_of_work)
    if isinstance(role_type, str):
        profession = _ROLE_PROFESSION.get(role_type)
        if profession:
            info.professions.add(profession)
        if role_type in _SOLOIST_ROLES and role_name:
            info.disciplines.add(role_name)


def _collect(concert: dict[str, Any], registry: dict[str, _Artist]) -> None:
    """Register every artist of a concert: the concert-level ones (orchestra,
    conductor) and the per-work ones (composer, soloist, arranger)."""
    for artist in concert.get("_links", {}).get("artist", []):
        _register(registry, artist)
    for work in concert.get("_embedded", {}).get("work", []):
        for artist in work.get("_links", {}).get("artist", []):
            _register(registry, artist)


def _artist_record(info: _Artist) -> SourceRecord:
    kind = "ensemble" if info.is_group else "person"
    claims: list[SourceClaim] = []
    if not info.is_group:
        claims += [SourceClaim("has_profession", "profession", p) for p in sorted(info.professions)]
        claims += [SourceClaim("performs_as", "discipline", d) for d in sorted(info.disciplines)]
    return SourceRecord(
        external_id=f"artist:{info.id}",
        name=info.name,
        url=f"{BASE_URL}/en/artist/{info.id}",
        raw={
            "id": info.id,
            "name": info.name,
            "professions": sorted(info.professions),
            "disciplines": sorted(info.disciplines),
        },
        kind=kind,
        claims=tuple(claims),
    )


def _artist_records(registry: dict[str, _Artist]) -> Iterator[SourceRecord]:
    for info in registry.values():
        yield _artist_record(info)
