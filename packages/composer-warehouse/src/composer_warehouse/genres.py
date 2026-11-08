"""Genres and movements: linking label-only entities, and the orphan report (#349).

A genre or movement Wikidata names is keyed on its QID (see
:func:`composer_models.normalize.entity_key`). Two kinds of entity still carry a
label key: the ones a non-Wikidata source names, and every one an older bronze
run of the ``wikidata`` source minted before the composer query asked for the
QID — a rebuild replays those runs too. :func:`link_label_genres` points each of
them at the QID-keyed entity of the same kind whose label (or English alias)
it carries, through the ``canonical_entity_id`` pointer gold already collapses
on, so "opera" from 2025 and ``wikidata:Q1344`` are one genre downstream.

:func:`genre_report` is the honest count of how far the hierarchy reaches: how
many entities have a ``subclass_of`` path to a top-level genre
(:mod:`composer_models.genres`), and the most-used ones that don't.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field

from composer_models import Claim, Entity
from composer_models.genres import GENRE_KINDS, MEMBERSHIP_PREDICATES, MUSICAL_FORM
from composer_models.normalize import dedup_key, entity_uuid, qid_key, wikidata_id
from sqlalchemy import select, update
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_QID_PREFIX = qid_key("")


@dataclass(frozen=True)
class GenreLinkStats:
    linked: int = 0
    ambiguous: int = 0
    unmatched: int = 0


def _unique(index: dict[str, set[uuid.UUID]], key: str) -> uuid.UUID | None:
    targets = index.get(key, set())
    return next(iter(targets)) if len(targets) == 1 else None


def link_label_genres(session: Session) -> GenreLinkStats:
    """Point every label-keyed genre/movement at the QID-keyed one it names.

    Recomputed from scratch on each run, so the outcome depends only on what
    silver holds, not on ingest order. A label match wins over an alias match;
    a label (or alias) two QID-keyed entities share links nothing, since
    picking one would be a guess.
    """
    session.execute(update(Entity).where(Entity.kind.in_(GENRE_KINDS)).values(canonical_entity_id=None))
    linked = ambiguous = unmatched = 0
    for kind in GENRE_KINDS:
        rows = session.execute(select(Entity.id, Entity.dedup_key, Entity.label).where(Entity.kind == kind))
        by_label: dict[str, set[uuid.UUID]] = {}
        legacy: list[tuple[uuid.UUID, str]] = []
        for entity_id, key, label in rows:
            if key.startswith(_QID_PREFIX):
                by_label.setdefault(dedup_key(label), set()).add(entity_id)
            else:
                legacy.append((entity_id, key))
        if not legacy:
            continue
        by_alias: dict[str, set[uuid.UUID]] = {}
        for entity_id, alias in session.execute(
            select(Claim.subject_id, Claim.value)
            .join(Entity, Entity.id == Claim.subject_id)
            .where(Entity.kind == kind, Claim.predicate == "also_known_as", Claim.value.is_not(None))
        ):
            if alias is not None:
                by_alias.setdefault(dedup_key(alias), set()).add(entity_id)

        for entity_id, key in legacy:
            target = _unique(by_label if key in by_label else by_alias, key)
            if target is not None:
                session.execute(
                    update(Entity).where(Entity.id == entity_id).values(canonical_entity_id=target)
                )
                linked += 1
            elif key in by_label or key in by_alias:
                ambiguous += 1
            else:
                unmatched += 1
    session.commit()
    session.expire_all()
    stats = GenreLinkStats(linked=linked, ambiguous=ambiguous, unmatched=unmatched)
    log.info("linked label-only genres/movements: %s", stats)
    return stats


@dataclass(frozen=True)
class OrphanGenre:
    label: str
    wikidata_id: str | None
    persons: int


@dataclass(frozen=True)
class GenreReport:
    kind: str
    total: int = 0
    qid_keyed: int = 0
    label_only: int = 0
    legacy_linked: int = 0
    with_parent: int = 0
    orphans: int = 0
    forms: int = 0
    tags: int = 0
    tags_reached: int = 0
    missing_top_level: list[str] = field(default_factory=list[str])
    top_orphans: list[OrphanGenre] = field(default_factory=list[OrphanGenre])
    top_forms: list[OrphanGenre] = field(default_factory=list[OrphanGenre])

    @property
    def reach(self) -> float:
        """The share of person tags whose entity has a path to a top level."""
        return self.tags_reached / self.tags if self.tags else 0.0


def genre_report(session: Session, kind: str, top_level: dict[str, str], limit: int = 10) -> GenreReport:
    """How well the ``kind`` hierarchy reaches the ``top_level`` QIDs.

    An orphan is an entity, other than a linked label-only one (it shares its
    canonical's place), with no ``subclass_of`` path up to a top-level entity:
    a QID-keyed genre whose ancestors all fell outside the vocabulary, or a
    label-only one nothing could be linked to. ``top_orphans`` ranks them by
    how many persons they tag, so the report says which gaps matter.

    A musical form with no such path ("symphony", whose Wikidata parent is
    "composed musical work") is not a gap in the genre tree but a term of
    another axis, so it is counted in ``forms`` and ranked in ``top_forms``
    instead (#364). ``tags`` counts distinct (person, entity) tags, linked
    label-only entities folded onto their canonical, and ``tags_reached`` the
    ones on an entity with a path to a top level.
    """
    entities = session.execute(
        select(Entity.id, Entity.dedup_key, Entity.label, Entity.canonical_entity_id).where(
            Entity.kind == kind
        )
    ).all()
    edges = session.execute(
        select(Claim.subject_id, Claim.object_id)
        .join(Entity, Entity.id == Claim.subject_id)
        .where(Entity.kind == kind, Claim.predicate == "subclass_of", Claim.object_id.is_not(None))
    )
    children: dict[uuid.UUID, set[uuid.UUID]] = {}
    with_parent: set[uuid.UUID] = set()
    for child, parent in edges:
        if parent is not None:
            children.setdefault(parent, set()).add(child)
            with_parent.add(child)

    ids = {entity_id for entity_id, *_ in entities}
    tops = {entity_uuid(kind, qid_key(qid)): qid for qid in top_level}
    reached = {top for top in tops if top in ids}
    stack = list(reached)
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in reached:
                reached.add(child)
                stack.append(child)

    canonical = {entity_id: target for entity_id, _, _, target in entities if target is not None}
    persons: Counter[uuid.UUID] = Counter()
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for subject, obj in session.execute(
        select(Claim.subject_id, Claim.object_id).where(
            Claim.predicate == MEMBERSHIP_PREDICATES[kind], Claim.object_id.in_(ids)
        )
    ):
        if obj is not None and (pair := (subject, canonical.get(obj, obj))) not in seen:
            seen.add(pair)
            persons[pair[1]] += 1

    form_ids = set(
        session.scalars(
            select(Claim.subject_id)
            .join(Entity, Entity.id == Claim.subject_id)
            .where(Entity.kind == kind, Claim.predicate == "genre_type", Claim.value == MUSICAL_FORM)
        )
    )
    unreached = [e for e, _, _, target in entities if target is None and e not in reached]
    orphans = [e for e in unreached if e not in form_ids]
    forms = [e for e in unreached if e in form_ids]
    labels = {entity_id: (label, key) for entity_id, key, label, _ in entities}

    def ranked(ids: list[uuid.UUID]) -> list[OrphanGenre]:
        top = sorted(ids, key=lambda e: (-persons[e], labels[e][0], str(e)))[:limit]
        return [OrphanGenre(labels[e][0], wikidata_id(labels[e][1]), persons[e]) for e in top]

    qid_keyed = sum(1 for _, key, _, _ in entities if key.startswith(_QID_PREFIX))
    return GenreReport(
        kind=kind,
        total=len(entities),
        qid_keyed=qid_keyed,
        label_only=len(entities) - qid_keyed,
        legacy_linked=len(canonical),
        with_parent=len(with_parent & ids),
        orphans=len(orphans),
        forms=len(forms),
        tags=len(seen),
        tags_reached=sum(count for entity_id, count in persons.items() if entity_id in reached),
        missing_top_level=sorted(qid for top, qid in tops.items() if top not in ids),
        top_orphans=ranked(orphans),
        top_forms=ranked(forms),
    )
