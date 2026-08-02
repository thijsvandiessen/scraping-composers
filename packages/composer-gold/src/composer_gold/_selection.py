"""Selection state of a gold build: dedup roots and the person curation rule."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from composer_warehouse.models import (
    Claim,
    ConcertParticipant,
    Entity,
    RawWorkMention,
    RecordingParticipant,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from .promote import PromoteConfig


def _appearance_counts(silver: Session, root: Callable[[uuid.UUID], uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many concerts and recordings each entity is actually credited on.

    Counted per dedup cluster (a duplicate spelling's concerts belong to the
    same musician) and per event, so the two spellings of one name on the same
    concert count once. Entities the derive passes could not resolve keep their
    verbatim participant name but link to nothing, so they count zero here —
    which is exactly the noise this drives out of gold.
    """
    appearances: dict[uuid.UUID, set[tuple[str, int]]] = {}
    for entity_id, concert_id in silver.execute(
        select(ConcertParticipant.entity_id, ConcertParticipant.concert_id).where(
            ConcertParticipant.entity_id.is_not(None)
        )
    ).tuples():
        if entity_id is not None:  # guaranteed by the WHERE; narrows the type
            appearances.setdefault(root(entity_id), set()).add(("concert", concert_id))
    for entity_id, recording_id in silver.execute(
        select(RecordingParticipant.entity_id, RecordingParticipant.recording_id).where(
            RecordingParticipant.entity_id.is_not(None)
        )
    ).tuples():
        if entity_id is not None:
            appearances.setdefault(root(entity_id), set()).add(("recording", recording_id))
    return {entity_id: len(events) for entity_id, events in appearances.items()}


def _resolve_roots(silver: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Map every canonical-linked person to its transitive canonical root."""
    links: dict[uuid.UUID, uuid.UUID] = {
        entity_id: canonical_id
        for entity_id, canonical_id in silver.execute(
            select(Entity.id, Entity.canonical_entity_id).where(Entity.canonical_entity_id.is_not(None))
        ).tuples()
        if canonical_id is not None  # guaranteed by the WHERE; narrows the type
    }
    roots: dict[uuid.UUID, uuid.UUID] = {}
    for start in links:
        node = start
        seen = {node}
        while node in links and links[node] not in seen:
            node = links[node]
            seen.add(node)
        roots[start] = node
    return roots


def _sitelink_roots(
    silver: Session,
    root: Callable[[uuid.UUID], uuid.UUID],
    all_persons: set[uuid.UUID],
    min_sitelinks: int | None,
) -> set[uuid.UUID]:
    """Person roots whose Wikipedia sitelink count reaches ``min_sitelinks``.

    Sitelink counts are stored as string literals on the ``sitelink_count``
    claim; the count is taken per dedup cluster (max across its members, so the
    best-documented spelling wins) and non-numeric values are ignored. Returns
    an empty set when no threshold is configured.
    """
    if min_sitelinks is None:
        return set()
    all_person_roots = {root(p) for p in all_persons}
    max_sitelinks: dict[uuid.UUID, int] = {}
    for subject_id, value in silver.execute(
        select(Claim.subject_id, Claim.value).where(Claim.predicate == "sitelink_count")
    ).tuples():
        if value is None:
            continue
        try:
            count = int(value)
        except ValueError:
            continue
        r = root(subject_id)
        if count > max_sitelinks.get(r, -1):
            max_sitelinks[r] = count
    return {r for r, count in max_sitelinks.items() if r in all_person_roots and count >= min_sitelinks}


class GoldBuild:
    """One promotion run: the selection state and counters shared by the
    copy phases driven from ``_build``."""

    def __init__(self, silver: Session, config: PromoteConfig) -> None:
        self.silver = silver
        self.config = config
        # --- rule 2 groundwork: duplicate clusters ------------------------
        # With the rule off, no links are resolved and every spelling stands
        # on its own (including for rule 1's evidence check).
        self.roots = _resolve_roots(silver) if config.collapse_duplicates else {}
        self.all_persons = set(silver.scalars(select(Entity.id).where(Entity.kind == "person")))
        self.all_ensembles = set(silver.scalars(select(Entity.id).where(Entity.kind == "ensemble")))
        # Concerts/recordings credited to each dedup root: rule 1's evidence for
        # persons and ensembles alike.
        self.appearance_counts = _appearance_counts(silver, self.root)
        self.evidence_roots: set[uuid.UUID] = set()
        self.appearance_roots: set[uuid.UUID] = set()
        self.sitelink_roots: set[uuid.UUID] = set()
        self.kept_ensembles: set[uuid.UUID] = set()
        self.unevidenced_ensembles: set[uuid.UUID] = set()
        self.kept_roots: set[uuid.UUID] = set()
        self.kept_members: set[uuid.UUID] = set()
        self.all_other: set[uuid.UUID] = set()
        self.kept_other: set[uuid.UUID] = set()
        self.claim_rows: list[dict[str, Any]] = []
        self.record_count = 0
        self.work_count = 0
        self.title_count = 0
        self.mention_count = 0
        self.concert_count = 0
        self.participant_links = 0
        self.unresolved_names: set[str] = set()
        self.recording_count = 0
        self.recording_participant_links = 0
        self.recording_unresolved_names: set[str] = set()

    def root(self, entity_id: uuid.UUID) -> uuid.UUID:
        return self.roots.get(entity_id, entity_id)

    def _appearance_roots(self, candidates: set[uuid.UUID]) -> set[uuid.UUID]:
        """Roots of ``candidates`` credited on at least ``min_appearances``
        concerts or recordings."""
        candidate_roots = {self.root(c) for c in candidates}
        return {
            entity_id
            for entity_id, count in self.appearance_counts.items()
            if entity_id in candidate_roots and count >= self.config.min_appearances
        }

    def select_persons(self) -> None:
        """Rule 1: keep person clusters with performance/work evidence (or a
        sitelink count clearing the configured threshold); with the rule off,
        keep everyone.

        Evidence is a *credit*: the person is a participant on at least
        ``min_appearances`` concerts or recordings, or they composed a work some
        source mentioned. Being listed in a source's artist index is not enough
        — those name lists are what filled gold with musicians who never appear
        on a programme.
        """
        if not self.config.drop_unevidenced_persons:
            self.kept_roots = {self.root(p) for p in self.all_persons}
            self.kept_members = {p for p in self.all_persons if self.root(p) in self.kept_roots}
            return
        mention_composers = set(
            self.silver.scalars(
                select(RawWorkMention.composer_entity_id)
                .where(RawWorkMention.composer_entity_id.is_not(None))
                .distinct()
            )
        )
        composer_roots = {self.root(p) for p in self.all_persons if p in mention_composers}
        self.appearance_roots = self._appearance_roots(self.all_persons)
        self.evidence_roots = composer_roots | self.appearance_roots

        # --- extra signal: culturally significant persons by sitelink count -
        # Wikipedia sitelink count (from Wikidata) is a proxy for significance.
        # When a threshold is set, a person clearing it is promoted even without
        # the performance/work evidence above; this only ever adds persons,
        # never drops.
        self.sitelink_roots = _sitelink_roots(
            self.silver, self.root, self.all_persons, self.config.min_sitelinks
        )

        self.kept_roots = self.evidence_roots | self.sitelink_roots
        self.kept_members = {p for p in self.all_persons if self.root(p) in self.kept_roots}

    def select_ensembles(self) -> None:
        """Rule 1 for ensembles: an orchestra or choir earns its place in gold
        the same way a musician does — by being credited on concerts or
        recordings. Sources publish full ensemble indexes, and the ones that
        never turn up on a programme are noise; rule 3 alone would keep any of
        them a kept person happens to reference. Sharing rule 1's toggle means
        ``--no-drop-unevidenced-persons`` still keeps everything."""
        if not self.config.drop_unevidenced_persons:
            self.kept_ensembles = {self.root(e) for e in self.all_ensembles}
            self.unevidenced_ensembles = set()
            return
        self.kept_ensembles = self._appearance_roots(self.all_ensembles)
        self.unevidenced_ensembles = {self.root(e) for e in self.all_ensembles} - self.kept_ensembles
