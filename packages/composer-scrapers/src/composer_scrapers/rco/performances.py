"""Extract work mentions from an RCO concert detail JSON.

The ``programme`` array holds one item per programme entry; interval entries
have no ``composer`` key and are skipped. Credits (conductor + soloists) are
concert-level, not per-work, so they are extracted once and attached to every
work mention from that concert.
"""

from __future__ import annotations

from typing import Any

from .. import SourceWorkMention


def _conductor(concert: dict[str, Any]) -> str | None:
    for credit in concert.get("credits", []):
        if credit.get("roleEn") == "conductor":
            return credit.get("name") or None
    return None


def _soloists(concert: dict[str, Any]) -> list[dict[str, str | None]]:
    return [
        {"name": credit.get("name", ""), "discipline": credit.get("roleEn")}
        for credit in concert.get("credits", [])
        if credit.get("roleEn") and credit.get("roleEn") != "conductor"
    ]


def performances(concert: dict[str, Any]) -> list[SourceWorkMention]:
    """Return one SourceWorkMention per non-interval programme item."""
    meta: dict[str, Any] = concert.get("meta", {})
    concert_id = meta.get("id", 0)
    slug = meta.get("slug", "")
    concert_title = concert.get("title", "")
    date = concert.get("start", "")
    venue = concert.get("location", "")
    website_urls: dict[str, Any] = concert.get("websiteUrls") or {}
    url: str = website_urls.get("en", "")
    conductor = _conductor(concert)
    soloists = _soloists(concert)

    mentions: list[SourceWorkMention] = []
    programme: list[dict[str, Any]] = concert.get("program") or []
    for idx, item in enumerate(programme):
        composer: str = item.get("relatedCredit") or ""
        title: str = item.get("nameEn", "")
        if not composer or composer.startswith("--") or not title:
            continue
        mentions.append(
            SourceWorkMention(
                external_id=f"perf:{concert_id}:{idx}",
                title=title,
                composer=composer,
                raw={
                    "slug": slug,
                    "concert_id": concert_id,
                    "concert_title": concert_title,
                    "date": date,
                    "venue": venue,
                    "conductor": conductor,
                    "soloists": soloists,
                    "title": title,
                    "composer": composer,
                    "instrumentation": item.get("instrumentation"),
                    "duration_minutes": item.get("durationMinutes"),
                    "programme_idx": idx,
                    "url": url,
                },
            )
        )
    return mentions


_performances = performances
