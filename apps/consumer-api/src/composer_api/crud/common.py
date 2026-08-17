import uuid

from composer_warehouse.models import Claim, Entity, EntityRecord, Source
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from ..schemas import ClaimOut

CONCERT_WORKS_CAP = 20

INCOMING_CLAIMS_CAP = 50

WORK_PROOF_CAP = 5


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
            source_url=record_url or src_url,
            source_external_id=record_external_id,
        )
        for pred, val, obj_label, obj_id, src, src_url, record_url, record_external_id in rows
    ]
