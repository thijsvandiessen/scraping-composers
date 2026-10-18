"""Reads over the gold database shared by its exporters (Kumu, Neo4j).

Each folds the claim and record tables into per-entity lookups, collapsing the
rows several sources contribute for the same fact.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Collection

from composer_models import Claim, EntityRecord, Source
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def object_claims(session: Session) -> dict[tuple[uuid.UUID, str, uuid.UUID], list[str]]:
    """(subject, predicate, object) → the source names asserting it."""
    rows = session.execute(
        select(Claim.subject_id, Claim.predicate, Claim.object_id, Source.name)
        .join(Source, Source.id == Claim.source_id)
        .where(Claim.object_id.is_not(None))
        .order_by(Claim.subject_id, Claim.predicate, Source.name)
    ).tuples()
    claims: dict[tuple[uuid.UUID, str, uuid.UUID], list[str]] = defaultdict(list)
    for subject_id, predicate, object_id, source_name in rows:
        assert object_id is not None
        sources = claims[(subject_id, predicate, object_id)]
        if source_name not in sources:
            sources.append(source_name)
    return dict(claims)


def literal_claims(session: Session, predicates: Collection[str]) -> dict[uuid.UUID, dict[str, list[str]]]:
    """subject → predicate → the distinct values sources gave it, for the
    literal ``predicates`` asked for.

    Sources disagree (two birth dates for the same person is routine), so the
    values are kept in "most sources first" order and the caller picks.
    """
    rows = session.execute(
        select(Claim.subject_id, Claim.predicate, Claim.value, func.count())
        .where(Claim.object_id.is_(None), Claim.value.is_not(None), Claim.predicate.in_(predicates))
        .group_by(Claim.subject_id, Claim.predicate, Claim.value)
    ).tuples()
    tallies: dict[uuid.UUID, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for subject_id, predicate, value, count in rows:
        assert value is not None
        tallies[subject_id][predicate][value] += count
    return {
        subject_id: {
            # Ties broken on the value itself so the export is reproducible.
            predicate: [value for value, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
            for predicate, counts in by_predicate.items()
        }
        for subject_id, by_predicate in tallies.items()
    }


def entity_sources(session: Session) -> dict[uuid.UUID, tuple[list[str], str | None]]:
    """entity → (source names that reported it, a URL one of them gave)."""
    rows = session.execute(
        select(EntityRecord.entity_id, Source.name, EntityRecord.url)
        .join(Source, Source.id == EntityRecord.source_id)
        .where(EntityRecord.entity_id.is_not(None))
        .order_by(EntityRecord.entity_id, Source.name)
    ).tuples()
    names: dict[uuid.UUID, list[str]] = defaultdict(list)
    urls: dict[uuid.UUID, str] = {}
    for entity_id, source_name, url in rows:
        assert entity_id is not None
        if source_name not in names[entity_id]:
            names[entity_id].append(source_name)
        if url and entity_id not in urls:
            urls[entity_id] = url
    return {entity_id: (source_names, urls.get(entity_id)) for entity_id, source_names in names.items()}
