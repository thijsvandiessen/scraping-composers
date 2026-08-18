"""The composer list this source is scoped to, read straight out of gold.db.

Unlike every other source here, ``imslp_works`` does not discover composers
from IMSLP itself — it only walks IMSLP for composers gold.db already knows
about. The query runs over the shared ``composer_models`` schema (the same
models the warehouse and gold tiers write with), against a read-only SQLite
connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from composer_models import Claim, Entity, EntityRecord, Source
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, aliased


@dataclass(frozen=True)
class GoldComposer:
    """One composer to enrich, as gold.db already knows them.

    ``known_imslp_url`` is a category URL an earlier IMSLP scrape already
    confirmed for this entity (via its ``entity_records`` row), if any.
    """

    entity_id: str
    label: str
    known_imslp_url: str | None


def composers(gold_db_path: str) -> list[GoldComposer]:
    """Every gold person entity whose profession looks like "composer".

    "Looks like" means the ``has_profession`` claim's object label contains
    "compos" — matching "composer", "composer, conductor", "composer/arranger",
    and so on — left-joined against any IMSLP category URL an earlier scrape
    already confirmed for the entity.
    """
    profession = aliased(Entity)
    known_imslp_url = (
        select(EntityRecord.url)
        .join(Source, EntityRecord.source_id == Source.id)
        .where(EntityRecord.entity_id == Entity.id, Source.name == "imslp")
        .order_by(EntityRecord.url)
        .limit(1)
        .correlate(Entity)
        .scalar_subquery()
    )
    composer_ids = (
        select(Claim.subject_id)
        .join(profession, Claim.object_id == profession.id)
        .where(Claim.predicate == "has_profession", profession.label.like("%compos%"))
    )
    query = (
        select(Entity.id, Entity.label, known_imslp_url)
        .where(Entity.kind == "person", Entity.id.in_(composer_ids))
        .order_by(Entity.label)
    )

    engine = create_engine(f"sqlite:///file:{quote(gold_db_path)}?mode=ro&uri=true")
    try:
        with Session(engine) as session:
            rows = session.execute(query).all()
    finally:
        engine.dispose()
    return [
        GoldComposer(entity_id=str(entity_id), label=label, known_imslp_url=url)
        for entity_id, label, url in rows
    ]
