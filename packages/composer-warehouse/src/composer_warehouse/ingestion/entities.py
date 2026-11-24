import uuid
from datetime import datetime

from composer_models import Entity, Source
from composer_models.normalize import entity_key, entity_uuid
from composer_schema import resolve_entity_kind
from sqlalchemy import select, update
from sqlalchemy.orm import Session


def get_or_create_source(session: Session, name: str, base_url: str) -> Source:
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None:
        source = Source(name=name, base_url=base_url)
        session.add(source)
        session.flush()
    return source


PLACEHOLDER_PERSON_KEYS = frozenset(
    {
        # no known composer
        "anon", "anonym", "anonyme", "anonimo", "anonymous", "anonymus",
        "traditional", "trad", "folk song",
        "unknown", "unknown artist", "unknown composer", "unknown composer author",
        "unbekannt", "onbekend", "inconnu", "nn",
        # not one person at all
        "various", "various artists", "various composers", "audience",
    }
)  # fmt: skip
"""Dedup keys of the names sources write where a person would go but none is
known: "Anonymous", "Traditional", "Various", "N.N.", a live album's
"Audience".

Sources keep them verbatim (deciding they are not people is curation, see
:mod:`composer_scrapers.nyphil.people`), so the warehouse decides here. Minted
as a person, each one collects every anonymous work, compilation and applause
track in the corpus (Anonymous alone had 3,907 credits) and enters the person
dedupe pass as a high-reach composer. Matching is exact on the whole key, so
the ensemble "Anonymous 4" and the theorist "Anonymous IV" stay people. The
attribution itself is not lost: a mention keeps it in ``raw_composer``."""


def is_placeholder_person(label: str) -> bool:
    """Whether ``label`` names no particular person (see :data:`PLACEHOLDER_PERSON_KEYS`)."""
    return entity_key("person", label, None) in PLACEHOLDER_PERSON_KEYS


def get_or_create_entity(
    session: Session,
    cache: dict[tuple[str, str], uuid.UUID],
    kind: str,
    label: str,
    wikidata_id: str | None = None,
) -> uuid.UUID:
    """The id of the entity *label* names, creating it on first sight.

    Every entity in silver is minted here — a source record, a claim's object,
    a mention's composer — which is why the one kind nobody reports reliably is
    settled here too: a participant credit reaches ingest as a ``person``
    whether it names a musician or an orchestra, so
    :func:`~composer_schema.resolve_entity_kind` re-reads the label before the
    kind is baked into the entity's uuid (see #174). The key follows the
    resolved kind: a genre or movement with a QID is keyed on the QID alone
    (see :func:`~composer_models.normalize.entity_key`).
    """
    kind = resolve_entity_kind(kind, label)
    key = entity_key(kind, label, wikidata_id)
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
