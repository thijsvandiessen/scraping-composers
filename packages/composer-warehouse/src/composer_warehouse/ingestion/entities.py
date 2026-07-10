import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Entity, Source
from ..normalize import dedup_key, entity_uuid


def get_or_create_source(session: Session, name: str, base_url: str) -> Source:
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None:
        source = Source(name=name, base_url=base_url)
        session.add(source)
        session.flush()
    return source


def get_or_create_entity(
    session: Session,
    cache: dict[tuple[str, str], uuid.UUID],
    kind: str,
    label: str,
    wikidata_id: str | None = None,
) -> uuid.UUID:
    key = dedup_key(label, wikidata_id)
    entity_id = cache.get((kind, key))
    if entity_id is None:
        entity_id = entity_uuid(kind, key)
        session.add(Entity(id=entity_id, kind=kind, dedup_key=key, label=label))
        cache[(kind, key)] = entity_id
    return entity_id


def flush_entity_timestamps(
    session: Session,
    seen_ids: set[uuid.UUID],
    edited_ids: set[uuid.UUID],
    now: datetime,
) -> None:
    """Bulk-update last_ingested_at / last_edited_at for entities touched in the current batch."""
    if seen_ids:
        session.execute(update(Entity).where(Entity.id.in_(seen_ids)).values(last_ingested_at=now))
    if edited_ids:
        session.execute(update(Entity).where(Entity.id.in_(edited_ids)).values(last_edited_at=now))
