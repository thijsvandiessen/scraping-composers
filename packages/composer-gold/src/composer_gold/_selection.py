"""Selection state of a gold build: dedup roots and the person curation rule."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from composer_models import (
    Claim,
    ConcertParticipant,
    ConcertWork,
    Entity,
    RawWorkMention,
    RecordingParticipant,
    RecordingWork,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from .promote import PromoteConfig


@dataclass(frozen=True)
class AppearanceCount:
    concerts: int = 0
    recordings: int = 0


def _appearance_counts(
    silver: Session, root: Callable[[uuid.UUID], uuid.UUID]
) -> dict[uuid.UUID, AppearanceCount]:
    """How many concerts and recordings each entity is actually credited on.

    Counted per dedup cluster (a duplicate spelling's concerts belong to the
    same musician) and per event, so the two spellings of one name on the same
    concert count once. Entities the derive passes could not resolve keep their
    verbatim participant name but link to nothing, so they count zero here —
    which is exactly the noise this drives out of gold. Concerts and recordings
    are tracked separately so rule 1 can hold each to its own threshold.
    """
    concert_events: dict[uuid.UUID, set[int]] = {}
    recording_events: dict[uuid.UUID, set[int]] = {}
    for entity_id, concert_id in silver.execute(
        select(ConcertParticipant.entity_id, ConcertParticipant.concert_id).where(
            ConcertParticipant.entity_id.is_not(None)
        )
    ).tuples():
        if entity_id is not None:  # guaranteed by the WHERE; narrows the type
            concert_events.setdefault(root(entity_id), set()).add(concert_id)
    for entity_id, recording_id in silver.execute(
        select(RecordingParticipant.entity_id, RecordingParticipant.recording_id).where(
            RecordingParticipant.entity_id.is_not(None)
        )
    ).tuples():
        if entity_id is not None:
            recording_events.setdefault(root(entity_id), set()).add(recording_id)
    roots = concert_events.keys() | recording_events.keys()
    return {
        entity_id: AppearanceCount(
            concerts=len(concert_events.get(entity_id, ())),
            recordings=len(recording_events.get(entity_id, ())),
        )
        for entity_id in roots
    }


@dataclass(frozen=True)
class ComposerReach:
    works: int = 0
    programmes: int = 0


def _composer_reach(
    silver: Session, root: Callable[[uuid.UUID], uuid.UUID]
) -> dict[uuid.UUID, ComposerReach]:
    """How far each composer's mentioned works reach: distinct works, and
    distinct concerts+recordings those works were programmed on.

    Counted per dedup cluster, like ``_appearance_counts``. A mention not yet
    matched to a work counts by its lowercased title, so unmatched mentions of
    one piece still count once. Concert and recording ids live in separate id
    spaces, so they are tagged before being pooled into one programme count.
    """
    works: dict[uuid.UUID, set[str]] = {}
    programmes: dict[uuid.UUID, set[tuple[str, int]]] = {}
    for composer_id, work_id, title in silver.execute(
        select(
            RawWorkMention.composer_entity_id, RawWorkMention.work_id, func.lower(RawWorkMention.raw_title)
        ).where(RawWorkMention.composer_entity_id.is_not(None))
    ).tuples():
        if composer_id is not None:  # guaranteed by the WHERE; narrows the type
            works.setdefault(root(composer_id), set()).add(str(work_id) if work_id else title)
    for tag, event, mention_id in (
        ("c", ConcertWork.concert_id, ConcertWork.mention_id),
        ("r", RecordingWork.recording_id, RecordingWork.mention_id),
    ):
        for composer_id, event_id in silver.execute(
            select(RawWorkMention.composer_entity_id, event)
            .join(RawWorkMention, RawWorkMention.id == mention_id)
            .where(RawWorkMention.composer_entity_id.is_not(None))
        ).tuples():
            if composer_id is not None:
                programmes.setdefault(root(composer_id), set()).add((tag, event_id))
    return {
        r: ComposerReach(works=len(works.get(r, ())), programmes=len(programmes.get(r, ())))
        for r in works.keys() | programmes.keys()
    }


def _resolve_roots(silver: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Map every duplicate to its cluster canonical.

    A one-hop read. The dedupe pass writes the column from an explicit
    partition (``composer_warehouse.persons.cluster``), so a canonical is never
    itself a duplicate — this used to walk the pointer chain with a cycle
    guard, because pairwise decisions could produce both chains and, in
    principle, cycles.
    """
    return {
        entity_id: canonical_id
        for entity_id, canonical_id in silver.execute(
            select(Entity.id, Entity.canonical_entity_id).where(Entity.canonical_entity_id.is_not(None))
        ).tuples()
        if canonical_id is not None  # guaranteed by the WHERE; narrows the type
    }


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

    def _appearance_roots(
        self, candidates: set[uuid.UUID], min_concert: int, min_recording: int
    ) -> set[uuid.UUID]:
        """Roots of ``candidates`` credited on at least ``min_concert`` concerts
        or at least ``min_recording`` recordings (either threshold alone
        suffices). Candidates absent from ``appearance_counts`` have zero of
        both, which still clears thresholds of 0."""
        candidate_roots = {self.root(c) for c in candidates}
        kept = set()
        for r in candidate_roots:
            count = self.appearance_counts.get(r, AppearanceCount())
            if count.concerts >= min_concert or count.recordings >= min_recording:
                kept.add(r)
        return kept

    def _composer_roots(
        self, candidates: set[uuid.UUID], min_appearances: int, min_works: int, min_programmes: int
    ) -> set[uuid.UUID]:
        """Roots of ``candidates`` (persons who composed a mentioned work) whose
        combined concert+recording credits clear ``min_appearances`` (zero, the
        default, exempts composers from that check) and whose works reach at
        least ``min_works`` distinct works or ``min_programmes`` distinct
        concerts+recordings."""
        candidate_roots = {self.root(c) for c in candidates}
        reach = _composer_reach(self.silver, self.root)
        kept = set()
        for r in candidate_roots:
            count = self.appearance_counts.get(r, AppearanceCount())
            composer = reach.get(r, ComposerReach())
            if count.concerts + count.recordings >= min_appearances and (
                composer.works >= min_works or composer.programmes >= min_programmes
            ):
                kept.add(r)
        return kept

    def select_persons(self) -> None:
        """Rule 1: keep person clusters with performance/work evidence (or a
        sitelink count clearing the configured threshold); with the rule off,
        keep everyone.

        Evidence is a *credit*: the person is a participant on enough concerts
        or recordings to clear the configured thresholds (either alone
        suffices), or they composed a work some source mentioned and clear the
        (separately configurable, often lower) composer threshold — plus enough
        distinct works, or enough concerts+recordings of those works, that a
        one-off placeholder name on a single programme doesn't pass. Being listed
        in a source's artist index is not enough — those name lists are what
        filled gold with musicians who never appear on a programme.
        """
        if not self.config.drop_unevidenced_persons:
            self.kept_roots = {self.root(p) for p in self.all_persons}
            self.kept_members = {p for p in self.all_persons if self.root(p) in self.kept_roots}
            return
        cfg = self.config.rule1.persons
        mention_composers = set(
            self.silver.scalars(
                select(RawWorkMention.composer_entity_id)
                .where(RawWorkMention.composer_entity_id.is_not(None))
                .distinct()
            )
        )
        composer_candidates = {p for p in self.all_persons if p in mention_composers}
        composer_roots = self._composer_roots(
            composer_candidates,
            cfg.min_appearances_for_composers,
            cfg.min_works_for_composers,
            cfg.min_programmes_for_composers,
        )
        self.appearance_roots = self._appearance_roots(
            self.all_persons, cfg.min_concert_appearances, cfg.min_recording_appearances
        )
        self.evidence_roots = composer_roots | self.appearance_roots

        # --- extra signal: culturally significant persons by sitelink count -
        # Wikipedia sitelink count (from Wikidata) is a proxy for significance.
        # When a threshold is set, a person clearing it is promoted even without
        # the performance/work evidence above; this only ever adds persons,
        # never drops.
        self.sitelink_roots = _sitelink_roots(self.silver, self.root, self.all_persons, cfg.min_sitelinks)

        self.kept_roots = self.evidence_roots | self.sitelink_roots
        self.kept_members = {p for p in self.all_persons if self.root(p) in self.kept_roots}

    def select_ensembles(self) -> None:
        """Rule 1 for ensembles: an orchestra or choir earns its place in gold
        the same way a musician does — by being credited on enough concerts or
        recordings to clear its own configured thresholds. Sources publish full
        ensemble indexes, and the ones that never turn up on a programme are
        noise; rule 3 alone would keep any of them a kept person happens to
        reference. Sharing rule 1's toggle means ``--no-drop-unevidenced-persons``
        still keeps everything."""
        if not self.config.drop_unevidenced_persons:
            self.kept_ensembles = {self.root(e) for e in self.all_ensembles}
            self.unevidenced_ensembles = set()
            return
        cfg = self.config.rule1.ensembles
        self.kept_ensembles = self._appearance_roots(
            self.all_ensembles, cfg.min_concert_appearances, cfg.min_recording_appearances
        )
        self.unevidenced_ensembles = {self.root(e) for e in self.all_ensembles} - self.kept_ensembles
