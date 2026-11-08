"""Link places a source named only by label to the Wikidata place they mean.

Wikidata's claims pin every place to its QID, which ingest folds into the dedup
key, so "Germany" from Wikidata is the entity ``germany|Q183``. A source with no
QIDs (classicalmusiconline's ``citizen_of``) creates the bare ``germany``
beside it. This pass points such a bare place at the pinned place with the same
name, through ``canonical_entity_id`` -- the link the gold build follows when it
re-points claims, as it does for duplicate persons.

The link is made only when exactly one pinned place has the name: "Georgia" the
country and the US state are both pinned, and a bare "Georgia" could mean
either, so it stays as it is. Like ``derive_concerts`` the pass starts over on
every run, so a place that became ambiguous loses its old link.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from composer_models import Entity
from composer_models.normalize import wikidata_id
from sqlalchemy import select, update
from sqlalchemy.orm import Session

PLACE_KIND = "place"


@dataclass(frozen=True)
class LinkPlacesStats:
    linked: int = 0
    ambiguous: int = 0
    unmatched: int = 0


def link_places(session: Session) -> LinkPlacesStats:
    """Point every bare place at the one pinned place sharing its name; commits."""
    session.execute(update(Entity).where(Entity.kind == PLACE_KIND).values(canonical_entity_id=None))
    pinned: dict[str, list[uuid.UUID]] = {}
    bare: list[tuple[uuid.UUID, str]] = []
    for entity_id, key in session.execute(
        select(Entity.id, Entity.dedup_key).where(Entity.kind == PLACE_KIND)
    ).tuples():
        qid = wikidata_id(key)
        if qid is None:
            bare.append((entity_id, key))
        else:
            pinned.setdefault(key.removesuffix(f"|{qid}"), []).append(entity_id)

    linked = ambiguous = unmatched = 0
    for entity_id, key in bare:
        targets = pinned.get(key, [])
        if len(targets) == 1:
            session.execute(
                update(Entity).where(Entity.id == entity_id).values(canonical_entity_id=targets[0])
            )
            linked += 1
        elif targets:
            ambiguous += 1
        else:
            unmatched += 1
    session.commit()
    return LinkPlacesStats(linked=linked, ambiguous=ambiguous, unmatched=unmatched)
