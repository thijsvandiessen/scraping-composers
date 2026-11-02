"""Point each source's ``work`` entity at the canonical work it describes.

A publisher catalogue or Wikidata emits two documents for one work: a work
mention, which the matcher resolves to a canonical :class:`~composer_models.Work`,
and a ``work`` entity, which carries what a mention cannot — ``written_for`` and
``includes_instrument`` claims, and the parts with their counts in its record's
``raw``. Nothing tied the two together, so a canonical work could not reach its
own instrumentation. :func:`link_work_entities` does, through the one thing the
two documents share: the source record they came from.

A mention belongs to an entity when the same source gave both the same external
id (Bärenreiter's and Henle's product ids), or gave the mention the entity's id
followed by ``:<composer QID>`` (a Wikidata work's mention per composer,
``Q1:Q7294``). An anthology's items (``<product>#<n>``) belong to no entity: the
anthology entity is not a work the matcher looks for.

The link follows the mention's decision, so it moves when a reviewer re-matches
the mention, and a mention still unmatched links nothing. Recomputed from
scratch on each run, like ``link_works``, so the result depends only on what
silver holds.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass

from composer_models import Entity, EntityRecord, RawWorkMention, WorkEntity
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

#: The suffix a Wikidata work source adds to the item's id for each composer.
_COMPOSER_SUFFIX = re.compile(r":Q\d+$")

_CHUNK = 500


@dataclass(frozen=True)
class WorkEntityStats:
    links: int = 0
    #: work entities with no mention resolved to a work (yet)
    unlinked: int = 0


def _record_key(external_id: str) -> str:
    """The entity external id a mention's external id belongs to."""
    return _COMPOSER_SUFFIX.sub("", external_id)


def link_work_entities(session: Session) -> WorkEntityStats:
    """Rebuild ``work_entities`` from the mentions each work entity's source
    records resolved; see the module docstring for the rule."""
    session.execute(delete(WorkEntity))
    entities: dict[tuple[int, str], set[uuid.UUID]] = defaultdict(set)
    for source_id, external_id, entity_id in session.execute(
        select(EntityRecord.source_id, EntityRecord.external_id, EntityRecord.entity_id)
        .join(Entity, Entity.id == EntityRecord.entity_id)
        .where(Entity.kind == "work")
    ):
        if entity_id is not None:
            entities[(source_id, external_id)].add(entity_id)

    links: set[tuple[uuid.UUID, uuid.UUID, int]] = set()
    source_ids = sorted({source_id for source_id, _ in entities})
    for source_id in source_ids:
        for external_id, work_id in session.execute(
            select(RawWorkMention.external_id, RawWorkMention.work_id).where(
                RawWorkMention.source_id == source_id, RawWorkMention.work_id.is_not(None)
            )
        ):
            if work_id is None:
                continue
            for entity_id in entities.get((source_id, _record_key(external_id)), ()):
                links.add((work_id, entity_id, source_id))

    rows = [
        {"work_id": work_id, "entity_id": entity_id, "source_id": source_id}
        for work_id, entity_id, source_id in sorted(
            links, key=lambda link: (str(link[0]), str(link[1]), link[2])
        )
    ]
    for i in range(0, len(rows), _CHUNK):
        session.execute(insert(WorkEntity), rows[i : i + _CHUNK])
    session.commit()

    linked = {entity_id for _, entity_id, _ in links}
    all_entities = {entity_id for ids in entities.values() for entity_id in ids}
    stats = WorkEntityStats(links=len(rows), unlinked=len(all_entities - linked))
    log.info("linked work entities to works: %s", stats)
    return stats
