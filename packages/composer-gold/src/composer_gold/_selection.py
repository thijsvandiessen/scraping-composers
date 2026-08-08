"""Selection state of a gold build: which entities the curation rules keep."""

from __future__ import annotations

import uuid
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


def _appearance_counts(silver: Session) -> dict[uuid.UUID, int]:
    """How many concerts and recordings each entity is actually credited on.

    Counted per event, so an entity credited twice on the same concert counts
    once. Entities the derive passes could not resolve keep their verbatim
    participant name but link to nothing, so they count zero here — which is
    exactly the noise this drives out of gold.
    """
    appearances: dict[uuid.UUID, set[tuple[str, int]]] = {}
    for entity_id, concert_id in silver.execute(
        select(ConcertParticipant.entity_id, ConcertParticipant.concert_id).where(
            ConcertParticipant.entity_id.is_not(None)
        )
    ).tuples():
        if entity_id is not None:  # guaranteed by the WHERE; narrows the type
            appearances.setdefault(entity_id, set()).add(("concert", concert_id))
    for entity_id, recording_id in silver.execute(
        select(RecordingParticipant.entity_id, RecordingParticipant.recording_id).where(
            RecordingParticipant.entity_id.is_not(None)
        )
    ).tuples():
        if entity_id is not None:
            appearances.setdefault(entity_id, set()).add(("recording", recording_id))
    return {entity_id: len(events) for entity_id, events in appearances.items()}


def _sitelink_persons(
    silver: Session, all_persons: set[uuid.UUID], min_sitelinks: int | None
) -> set[uuid.UUID]:
    """Persons whose Wikipedia sitelink count reaches ``min_sitelinks``.

    Sitelink counts are stored as string literals on the ``sitelink_count``
    claim; non-numeric values are ignored, and the highest count a person
    carries wins. Returns an empty set when no threshold is configured.
    """
    if min_sitelinks is None:
        return set()
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
        if count > max_sitelinks.get(subject_id, -1):
            max_sitelinks[subject_id] = count
    return {p for p, count in max_sitelinks.items() if p in all_persons and count >= min_sitelinks}


class GoldBuild:
    """One promotion run: the selection state and counters shared by the
    copy phases driven from ``_build``."""

    def __init__(self, silver: Session, config: PromoteConfig) -> None:
        self.silver = silver
        self.config = config
        self.all_persons = set(silver.scalars(select(Entity.id).where(Entity.kind == "person")))
        self.all_ensembles = set(silver.scalars(select(Entity.id).where(Entity.kind == "ensemble")))
        # Concerts/recordings credited to each entity: rule 1's evidence for
        # persons and ensembles alike.
        self.appearance_counts = _appearance_counts(silver)
        self.evidence_persons: set[uuid.UUID] = set()
        self.appearance_persons: set[uuid.UUID] = set()
        self.sitelink_persons: set[uuid.UUID] = set()
        self.kept_ensembles: set[uuid.UUID] = set()
        self.unevidenced_ensembles: set[uuid.UUID] = set()
        self.kept_persons: set[uuid.UUID] = set()
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

    def _credited(self, candidates: set[uuid.UUID]) -> set[uuid.UUID]:
        """The ``candidates`` credited on at least ``min_appearances`` concerts
        or recordings."""
        return {
            entity_id
            for entity_id, count in self.appearance_counts.items()
            if entity_id in candidates and count >= self.config.min_appearances
        }

    def select_persons(self) -> None:
        """Rule 1: keep persons with performance/work evidence (or a sitelink
        count clearing the configured threshold); with the rule off, keep
        everyone.

        Evidence is a *credit*: the person is a participant on at least
        ``min_appearances`` concerts or recordings, or they composed a work some
        source mentioned. Being listed in a source's artist index is not enough
        — those name lists are what filled gold with musicians who never appear
        on a programme.
        """
        if not self.config.drop_unevidenced_persons:
            self.kept_persons = set(self.all_persons)
            return
        mention_composers = set(
            self.silver.scalars(
                select(RawWorkMention.composer_entity_id)
                .where(RawWorkMention.composer_entity_id.is_not(None))
                .distinct()
            )
        )
        composers = self.all_persons & mention_composers
        self.appearance_persons = self._credited(self.all_persons)
        self.evidence_persons = composers | self.appearance_persons

        # --- extra signal: culturally significant persons by sitelink count -
        # Wikipedia sitelink count (from Wikidata) is a proxy for significance.
        # When a threshold is set, a person clearing it is promoted even without
        # the performance/work evidence above; this only ever adds persons,
        # never drops.
        self.sitelink_persons = _sitelink_persons(self.silver, self.all_persons, self.config.min_sitelinks)

        self.kept_persons = self.evidence_persons | self.sitelink_persons

    def select_ensembles(self) -> None:
        """Rule 1 for ensembles: an orchestra or choir earns its place in gold
        the same way a musician does — by being credited on concerts or
        recordings. Sources publish full ensemble indexes, and the ones that
        never turn up on a programme are noise; rule 3 alone would keep any of
        them a kept person happens to reference. Sharing rule 1's toggle means
        ``--no-drop-unevidenced-persons`` still keeps everything."""
        if not self.config.drop_unevidenced_persons:
            self.kept_ensembles = set(self.all_ensembles)
            self.unevidenced_ensembles = set()
            return
        self.kept_ensembles = self._credited(self.all_ensembles)
        self.unevidenced_ensembles = self.all_ensembles - self.kept_ensembles
