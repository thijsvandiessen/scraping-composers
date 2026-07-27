"""Selection state of a gold build: dedup roots and the person curation rule."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from composer_warehouse.models import Claim, Entity, EntityRecord, RawWorkMention
from sqlalchemy import select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from .promote import PromoteConfig


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
        self.evidence_roots: set[uuid.UUID] = set()
        self.sitelink_roots: set[uuid.UUID] = set()
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

    def select_persons(self) -> None:
        """Rule 1: keep person clusters with performance/work evidence (or a
        sitelink count clearing the configured threshold); with the rule off,
        keep everyone."""
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
        perf_sources = select(RawWorkMention.source_id).distinct().scalar_subquery()
        archive_reported = set(
            self.silver.scalars(
                select(EntityRecord.entity_id)
                .where(EntityRecord.source_id.in_(perf_sources), EntityRecord.entity_id.is_not(None))
                .distinct()
            )
        )
        evidence = mention_composers | archive_reported
        self.evidence_roots = {self.root(p) for p in self.all_persons if p in evidence}

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
