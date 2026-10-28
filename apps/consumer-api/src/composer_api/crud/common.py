import json
import uuid

from composer_models import Claim, Entity, EntityRecord, Source
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from ..schemas import ClaimOut

CONCERT_WORKS_CAP = 20

INCOMING_CLAIMS_CAP = 50

WORK_PROOF_CAP = 5


def mention_url(raw: str) -> str | None:
    """The page a mention was scraped from, as its source recorded it.

    Scrapers keep the work's own page (a catalogue entry, a production page)
    under ``url`` in the raw payload; a proof without a concert or recording
    falls back to it rather than to no link at all.
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    url = payload.get("url") if isinstance(payload, dict) else None
    return url if isinstance(url, str) and url else None


def deep_link(url: str | None, base_url: str | None) -> str | None:
    """``url`` when it points at a specific page, else ``None``.

    A link to the source's homepage (a record stored without a page of its own,
    or a source like Open Opus whose records only know the homepage) tells the
    reader nothing they can check, so the frontend shows the source unlinked.
    """
    if not url or (base_url and url.rstrip("/") == base_url.rstrip("/")):
        return None
    return url


def outgoing_claims(db: Session, entity_id: uuid.UUID) -> list[ClaimOut]:
    obj = aliased(Entity)
    rows = db.execute(
        select(
            Claim.predicate,
            Claim.value,
            obj.label,
            Claim.object_id,
            Source.name,
            Source.base_url,
            EntityRecord.url,
            EntityRecord.external_id,
        )
        .join(Source, Source.id == Claim.source_id)
        .outerjoin(obj, obj.id == Claim.object_id)
        # Outer join: gold copies records only for kept entities, so a claim's
        # record_id may point at a record that wasn't promoted.
        .outerjoin(EntityRecord, EntityRecord.id == Claim.record_id)
        .where(Claim.subject_id == entity_id)
        .order_by(Claim.predicate, Source.name)
    ).all()
    return [
        ClaimOut(
            predicate=pred,
            value=val,
            object_label=obj_label,
            object_id=obj_id,
            source=src,
            source_url=deep_link(record_url, src_url),
            source_external_id=record_external_id,
        )
        for pred, val, obj_label, obj_id, src, src_url, record_url, record_external_id in rows
    ]
