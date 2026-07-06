"""Person records from the RCO conductors page and concert credits.

Two sources of person data:

1. The conductors page yields rich profiles: biography, function label
   ("chief conductor 2016-2018"), stable credit ID, portrait image and profile URL.
2. Concert ``credits[]`` yield leaner records for every credited conductor and
   soloist (name + role), accumulated across all concerts to track appearance counts.

Both sources yield ``person`` records that the dedup pipeline merges by name,
so a conductor known from the profile page and one seen in concert credits become
one Entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import SourceClaim, SourceRecord
from .fetch import BASE_URL


@dataclass
class _Credit:
    """A credited person accumulated across concerts."""

    name: str
    role_en: str
    image_url: str | None
    profile_url: str | None = None
    concert_ids: set[int] = field(default_factory=set)


def iter_conductor_records(page_json: dict[str, Any]) -> list[SourceRecord]:
    """Extract one SourceRecord per conductor from the conductors overview JSON."""
    records = []
    for block in page_json.get("content", []):
        if block.get("type") != "person_group":
            continue
        for person_page in block.get("persons", []):
            person = person_page.get("person", {})
            person_meta = person.get("meta", {})
            credit_id = person_meta.get("id")
            if not credit_id:
                continue
            name = (person.get("name") or "").strip()
            if not name:
                continue

            role = (person.get("role") or {}).get("label") or "conductor"
            function_obj = person.get("function") or {}
            function_label: str | None = function_obj.get("label") or None
            asset = person.get("defaultAsset") or {}
            renditions = asset.get("renditions") or {}
            image_url: str | None = renditions.get("600x600") or asset.get("url") or None
            description = person.get("description") or ""
            ref_id = person_meta.get("referenceId") or ""
            page_url = person_page.get("url") or ""

            claims: list[SourceClaim] = [
                SourceClaim("has_profession", "profession", role),
            ]
            if function_label:
                claims.append(SourceClaim("has_function", value=function_label))

            records.append(
                SourceRecord(
                    external_id=f"credit:{credit_id}",
                    name=name,
                    url=f"{BASE_URL}{page_url}" if page_url else None,
                    raw={
                        "credit_id": credit_id,
                        "reference_id": ref_id,
                        "name": name,
                        "role": role,
                        "function": function_label,
                        "image_url": image_url,
                        "description": description,
                    },
                    kind="person",
                    claims=tuple(claims),
                )
            )
    return records


def collect_credits(concert: dict[str, Any], registry: dict[str, _Credit]) -> None:
    """Register credited persons from a concert detail dict."""
    concert_id: int = (concert.get("meta") or {}).get("id") or 0
    for credit in concert.get("credits", []):
        name = (credit.get("name") or "").strip()
        role_en = (credit.get("roleEn") or "").strip()
        if not name or not role_en:
            continue
        key = f"{role_en}:{name}"
        if key not in registry:
            renditions = credit.get("imageRenditions") or {}
            registry[key] = _Credit(
                name=name,
                role_en=role_en,
                image_url=renditions.get("600x600"),
                profile_url=credit.get("url"),
            )
        registry[key].concert_ids.add(concert_id)


def credit_record(credit: _Credit) -> SourceRecord:
    """Convert an accumulated credit to a SourceRecord."""
    is_conductor = credit.role_en == "conductor"
    profession = "conductor" if is_conductor else "soloist"
    claims: list[SourceClaim] = [
        SourceClaim("has_profession", "profession", profession),
    ]
    if not is_conductor:
        claims.append(SourceClaim("performs_as", value=credit.role_en))
    profile_url = f"{BASE_URL}{credit.profile_url}" if credit.profile_url else None
    return SourceRecord(
        external_id=f"credit:{credit.role_en}:{credit.name}",
        name=credit.name,
        url=profile_url,
        raw={
            "name": credit.name,
            "role": credit.role_en,
            "image_url": credit.image_url,
            "concert_count": len(credit.concert_ids),
        },
        kind="person",
        claims=tuple(claims),
    )
