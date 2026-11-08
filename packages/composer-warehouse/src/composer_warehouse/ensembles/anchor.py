"""Fold name-keyed ensembles onto the Wikidata ensembles they name (#350).

Every concert and recording source mints one ensemble per printed name, so one
orchestra sits in silver under each spelling the sources use. The
``wikidata_ensembles`` source supplies *anchors* — ensembles keyed on their QID
(see :func:`composer_models.normalize.entity_key`) — and with them every label
and alias the item has. This pass points each name-keyed ensemble whose match
key (:func:`.names.ensemble_match_key`) is exactly one anchor's name at that
anchor through ``canonical_entity_id``, the same non-destructive link the
person dedupe writes: silver keeps every spelling, gold collapses them.

Nothing fuzzy: a name either is one of the anchor's names or it is left alone,
which is what keeps "Wiener Philharmoniker Streichquartett" and "Junge
Philharmonie Wien" apart from the orchestra. A name two anchors share is only
linked when the anchors' home cities tell them apart; "Members of X" is a
group of its own and never folded onto X.

No human decides ensemble links, so the pass owns the column for the kind:
every run starts by clearing it, which makes it idempotent and lets a link
disappear once its evidence does.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from composer_models import Claim, ConcertParticipant, Entity, RecordingParticipant
from composer_models.normalize import dedup_key, wikidata_id
from sqlalchemy import select, update
from sqlalchemy.orm import Session, aliased

from .names import ensemble_match_key, members_of

log = logging.getLogger(__name__)

ENSEMBLE = "ensemble"


@dataclass(frozen=True)
class AnchorStats:
    """What one pass decided. The boundary counts are a report, not a move:
    they say how many person entities share a name with a Wikidata ensemble,
    and how many name-keyed ensembles share one with a Wikidata person."""

    anchors: int = 0
    anchored: int = 0
    ambiguous: int = 0
    unanchored: int = 0
    unanchored_with_appearances: int = 0
    members_of_kept_separate: int = 0
    persons_named_like_anchors: int = 0
    ensembles_named_like_persons: int = 0


def _anchor_names(session: Session, anchors: dict[uuid.UUID, str]) -> dict[str, set[uuid.UUID]]:
    """Match key -> the anchors that carry that name, as label or alias."""
    index: dict[str, set[uuid.UUID]] = {}
    for anchor_id, label in anchors.items():
        index.setdefault(ensemble_match_key(label), set()).add(anchor_id)
    for subject_id, value in session.execute(
        select(Claim.subject_id, Claim.value).where(
            Claim.predicate == "also_known_as",
            Claim.subject_id.in_(select(Entity.id).where(Entity.kind == ENSEMBLE)),
        )
    ).tuples():
        if value and subject_id in anchors:
            index.setdefault(ensemble_match_key(value), set()).add(subject_id)
    return index


def _load_ensembles(session: Session) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, tuple[str, str]]]:
    """(anchor -> label, name-keyed ensemble -> (label, dedup key))."""
    anchors: dict[uuid.UUID, str] = {}
    name_keyed: dict[uuid.UUID, tuple[str, str]] = {}
    for entity_id, label, key in session.execute(
        select(Entity.id, Entity.label, Entity.dedup_key).where(Entity.kind == ENSEMBLE)
    ).tuples():
        if wikidata_id(key) is not None:
            anchors[entity_id] = label
        else:
            name_keyed[entity_id] = (label, key)
    return anchors, name_keyed


def _home_cities(session: Session, anchor_ids: set[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    """Anchor -> the dedup keys of the places it is ``based_in``."""
    place = aliased(Entity)
    cities: dict[uuid.UUID, set[str]] = {}
    for subject_id, label in session.execute(
        select(Claim.subject_id, place.label)
        .join(place, place.id == Claim.object_id)
        .where(Claim.predicate == "based_in")
    ).tuples():
        if subject_id in anchor_ids:
            cities.setdefault(subject_id, set()).add(dedup_key(label))
    return cities


def _city_tie_break(
    name: str, candidates: set[uuid.UUID], cities: dict[uuid.UUID, set[str]]
) -> uuid.UUID | None:
    """The one candidate whose home city the name itself spells out, if exactly one does."""
    padded = f" {dedup_key(name)} "
    named = [c for c in candidates if any(f" {city} " in padded for city in cities.get(c, ()))]
    return named[0] if len(named) == 1 else None


def _with_appearances(session: Session) -> set[uuid.UUID]:
    """Entities credited on a concert or recording, as of the last derive."""
    credited: set[uuid.UUID] = set()
    for column in (ConcertParticipant.entity_id, RecordingParticipant.entity_id):
        credited.update(e for e in session.scalars(select(column).where(column.is_not(None)).distinct()) if e)
    return credited


def alias_index(session: Session) -> dict[str, uuid.UUID]:
    """Match key -> anchor, for the names exactly one anchor carries.

    The derive passes' last resort for a credited name no source minted an
    entity for: the anchor itself is keyed on its QID, so a credit that spells
    one of its names would otherwise resolve to nothing.
    """
    anchors, _ = _load_ensembles(session)
    return {key: next(iter(ids)) for key, ids in _anchor_names(session, anchors).items() if len(ids) == 1}


def _boundary(
    session: Session, index: dict[str, set[uuid.UUID]], name_keyed: dict[uuid.UUID, tuple[str, str]]
) -> tuple[int, int]:
    """(persons named like an anchor, name-keyed ensembles named like a Wikidata person)."""
    persons = session.execute(select(Entity.label, Entity.dedup_key).where(Entity.kind == "person")).tuples()
    persons_like_anchors = 0
    wikidata_person_names: set[str] = set()
    for label, key in persons:
        if ensemble_match_key(label) in index:
            persons_like_anchors += 1
        if wikidata_id(key) is not None:
            wikidata_person_names.add(key.rsplit("|", 1)[0])
    ensembles_like_persons = sum(1 for _, key in name_keyed.values() if key in wikidata_person_names)
    return persons_like_anchors, ensembles_like_persons


def anchor_ensembles(session: Session) -> AnchorStats:
    """Point every name-keyed ensemble that names exactly one anchor at it."""
    session.execute(update(Entity).where(Entity.kind == ENSEMBLE).values(canonical_entity_id=None))

    anchors, name_keyed = _load_ensembles(session)
    index = _anchor_names(session, anchors)
    cities = _home_cities(session, set(anchors))
    credited = _with_appearances(session)

    links: list[dict[str, uuid.UUID]] = []
    ambiguous = members = unanchored = unanchored_credited = 0
    for entity_id, (label, _) in name_keyed.items():
        if members_of(label) is not None:
            members += 1
            continue
        candidates = index.get(ensemble_match_key(label), set())
        target = next(iter(candidates)) if len(candidates) == 1 else None
        if len(candidates) > 1:
            target = _city_tie_break(label, candidates, cities)
            if target is None:
                ambiguous += 1
                continue
        if target is None:
            unanchored += 1
            unanchored_credited += int(entity_id in credited)
            continue
        links.append({"id": entity_id, "canonical_entity_id": target})
    if links:
        session.execute(update(Entity), links)
    persons_like_anchors, ensembles_like_persons = _boundary(session, index, name_keyed)
    session.commit()

    stats = AnchorStats(
        anchors=len(anchors),
        anchored=len(links),
        ambiguous=ambiguous,
        unanchored=unanchored,
        unanchored_with_appearances=unanchored_credited,
        members_of_kept_separate=members,
        persons_named_like_anchors=persons_like_anchors,
        ensembles_named_like_persons=ensembles_like_persons,
    )
    log.info("ensemble anchors: %s", stats)
    return stats
