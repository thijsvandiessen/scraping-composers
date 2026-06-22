"""One work mention per work performed in a concert.

A concert's ``_embedded.work`` list holds its programme in order; each work
carries its composer(s) and per-work soloists in ``_links.artist`` (tagged by
``role.type``: ``composer``, ``instrument``/``voice`` for soloists,
``arrangement`` for arrangers), its conductor(s) and orchestra(s) in the
``name_conductor``/``name_orchestra`` lists, and its musical period in
``_links.epoch``. Each titled work becomes a ``SourceWorkMention`` (first
composer + title) for the resolution pipeline; the conductors, soloists (with
their instrument/voice), arrangers, orchestras, periods, date and concert URL
are kept verbatim in ``raw`` for a later performances pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ...document import Document, work_mention_document
from .fetch import BASE_URL

# Berlin local time: a concert's start timestamp is a Unix instant, and the
# performance's calendar date is the one in the hall.
_BERLIN = ZoneInfo("Europe/Berlin")

# role.type values that mark an instrumental/vocal soloist
_SOLOIST_ROLES = frozenset({"instrument", "voice"})


def _concert_date(concert: dict[str, Any]) -> str:
    begin = concert.get("date", {}).get("begin")
    if not begin:
        return ""
    return datetime.fromtimestamp(begin, _BERLIN).date().isoformat()


def _names_by_role(work: dict[str, Any], types: Iterable[str]) -> list[str]:
    types = set(types)
    return [
        a["name"]
        for a in work.get("_links", {}).get("artist", [])
        if a.get("name") and a.get("role", {}).get("type") in types
    ]


def _clean(names: Iterable[Any] | None) -> list[str]:
    return [n for n in (names or []) if n]


def _soloists_with_discipline(work: dict[str, Any]) -> list[dict[str, str | None]]:
    """Soloists with the instrument/voice from their ``role.name`` (e.g. violin)."""
    return [
        {"name": a["name"], "discipline": a.get("role", {}).get("name") or None}
        for a in work.get("_links", {}).get("artist", [])
        if a.get("name") and a.get("role", {}).get("type") in _SOLOIST_ROLES
    ]


def _performance_record(
    concert: dict[str, Any], season: str, work: dict[str, Any]
) -> Document | None:
    title = (work.get("title") or "").strip()
    if not title:
        return None
    work_id = work["id"]

    # composers come tagged in _links.artist; fall back to the flat name list
    composers = _names_by_role(work, {"composer"}) or _clean(work.get("name_composers"))
    soloists = _soloists_with_discipline(work)
    arrangers = _names_by_role(work, {"arrangement"})
    conductors = _clean(work.get("name_conductor"))
    ensembles = _clean(work.get("name_orchestra"))
    periods = [e["name"] for e in work.get("_links", {}).get("epoch", []) if e.get("name")]
    date = _concert_date(concert)

    return work_mention_document(
        id=f"perf:{work_id}",
        title=title,
        composer=composers[0] if composers else None,
        raw={
            "concert_id": concert["id"],
            "work_id": work_id,
            "title": title,
            "url": f"{BASE_URL}/en/concert/{concert['id']}",
            "date": date,
            "season": season,
            "composers": composers,
            "conductors": conductors,
            "soloists": soloists,
            "arrangers": arrangers,
            "ensembles": ensembles,
            "periods": periods,
            "is_encore": bool(work.get("is_encore")),
        },
    )


def _performances(concert: dict[str, Any]) -> Iterator[Document]:
    """Yield one work mention per titled work in the concert's programme."""
    season = next((s.get("label") for s in concert.get("_links", {}).get("season", [])), "")
    for work in concert.get("_embedded", {}).get("work", []):
        record = _performance_record(concert, season or "", work)
        if record is not None:
            yield record
