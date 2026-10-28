"""Fold together the works a composer's duplicate entities split apart (#362).

Ingest matches a mention only against the works of its own composer entity,
and on a rebuild every mention is ingested before ``dedupe_persons`` has linked
a single person. So when one composer is filed under two entities --
"Wolfgang Amadeus Mozart" and the surname-only "Mozart" -- each gets its own
*Symphony No. 40, K. 550*, and nothing after ingest brings them back together.

This pass does, once persons are deduped. It is the matching ingest would have
done had the persons been linked from the start:

- **Within a canonical composer.** The works of every entity in one person
  cluster are matched as one catalogue. The canonical entity's works are the
  base (or, when it has none, the works of the entity with the most); each
  work of the others is matched against what is there so far (the base's
  works plus the ones already added), with :func:`~.match.best_match` and the
  auto-match threshold, exactly as a mention would be. A match folds the work
  into its target; anything else joins the pool unchanged.
- **A work with no composer.** A title such as "Symfonie nr. 40 in g kl.t.,
  KV 550" whose source named no composer still carries a number that belongs
  to one: Köchel is Mozart's. Such a work is matched against the composer that
  *owns* the scheme -- the canonical composer holding more than half of the
  scheme's numbers -- and only when that composer holds this very number. A
  scheme shared by several composers (``WoO``, an opus) has no owner, and the
  work stays where it is.

Folding a work moves its mentions (and the review queue's pointers to it), its
titles and its identifiers onto the target, keeps the earlier premiere, and
deletes it. ``work_links`` is rebuilt afterwards by ``link_works``. The pass
is idempotent: a second run finds nothing left to fold.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from composer_models import RawWorkMention, Work, WorkIdentifier, WorkLink, WorkTitle
from composer_schema.premiere import supersedes
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from . import Candidate, candidate_rows, composer_canonicals, refresh_candidate
from .match import best_match, classify
from .schemes import is_composer_scoped

#: The share of a scheme's numbers one composer must hold to own the scheme.
OWNER_SHARE = 0.5


@dataclass(frozen=True)
class MergeStats:
    # works folded into another work of the same canonical composer
    within_composer: int = 0
    # composerless works folded into a work of the scheme's owner
    composerless: int = 0

    @property
    def merged(self) -> int:
        return self.within_composer + self.composerless


def _order(candidates: list[Candidate]) -> list[Candidate]:
    """A fixed order to match in, so the result is a function of the works."""
    return sorted(candidates, key=lambda c: (c.features.normalized_title, str(c.work_id)))


def _fold_round(
    composers: list[uuid.UUID],
    by_composer: dict[uuid.UUID, list[Candidate]],
    merges: dict[uuid.UUID, uuid.UUID],
) -> list[Candidate]:
    """One pass over a canonical composer's entities, base first; the pool left."""
    pool = list(by_composer.get(composers[0], []))
    for composer in composers[1:]:
        for cand in _order(by_composer.get(composer, [])):
            value, _method, target = best_match(cand.features, pool)
            if target is not None and classify(value) == "auto_matched":
                merges[cand.work_id] = target
                refresh_candidate(pool, target, cand.features.identifiers)
            else:
                pool.append(cand)
    return pool


def _fold_group(
    canonical: uuid.UUID, by_composer: dict[uuid.UUID, list[Candidate]], merges: dict[uuid.UUID, uuid.UUID]
) -> list[Candidate]:
    """Match one canonical composer's entities as one catalogue; the pool left.
    The canonical entity's own works are the base when it has any, else the
    entity with the most works.

    Repeated until nothing folds: a work that gains a number by absorbing
    another can match a base work it did not match before, and a second run
    of the pass must find nothing left to do.
    """
    composer_of = {cand.work_id: composer for composer, cands in by_composer.items() for cand in cands}
    # The order is fixed once: a base work is never folded into another, so
    # the folds form no cycle across rounds.
    composers = sorted(by_composer, key=lambda c: (c != canonical, -len(by_composer[c]), str(c)))
    while True:
        before = len(merges)
        pool = _fold_round(composers, by_composer, merges)
        if len(merges) == before or len(composers) == 1:
            return pool
        by_composer = {}
        for cand in pool:
            by_composer.setdefault(composer_of[cand.work_id], []).append(cand)


def _owners(pools: dict[uuid.UUID, list[Candidate]]) -> dict[str, uuid.UUID]:
    """Each composer-scoped scheme's owner: the canonical composer holding more
    than :data:`OWNER_SHARE` of the numbers any composer holds in it."""
    values: dict[str, dict[uuid.UUID, set[str]]] = {}
    for composer, pool in pools.items():
        for cand in pool:
            for scheme, value in cand.features.identifiers:
                if is_composer_scoped(scheme):
                    values.setdefault(scheme, {}).setdefault(composer, set()).add(value)
    owners: dict[str, uuid.UUID] = {}
    for scheme, held in values.items():
        everything = set[str]().union(*held.values())
        composer = min(held, key=lambda c: (-len(held[c]), str(c)))
        if len(held[composer]) > OWNER_SHARE * len(everything):
            owners[scheme] = composer
    return owners


def _fold_composerless(
    orphans: list[Candidate], pools: dict[uuid.UUID, list[Candidate]], merges: dict[uuid.UUID, uuid.UUID]
) -> int:
    """Fold each composerless work into its scheme owner's matching work."""
    owners = _owners(pools)
    held: dict[uuid.UUID, set[tuple[str, str]]] = {
        composer: {identifier for cand in pool for identifier in cand.features.identifiers}
        for composer, pool in pools.items()
        if composer in owners.values()
    }
    folded = 0
    for cand in _order(orphans):
        targets = {
            owners[scheme]
            for scheme, value in cand.features.identifiers
            if scheme in owners and (scheme, value) in held[owners[scheme]]
        }
        if len(targets) != 1:
            continue
        pool = pools[targets.pop()]
        value, _method, target = best_match(cand.features, pool)
        if target is not None and classify(value) == "auto_matched":
            merges[cand.work_id] = target
            refresh_candidate(pool, target, cand.features.identifiers)
            folded += 1
    return folded


#: Works folded per round of statements (bounded so ``IN`` lists stay small).
CHUNK = 500


def _move_rows(session: Session, merges: dict[uuid.UUID, uuid.UUID], chunk: list[uuid.UUID]) -> None:
    """Point one chunk of folded works' mentions, titles and identifiers at
    their targets, dropping a title or identifier the target already has."""
    mentions = session.execute(
        select(RawWorkMention.id, RawWorkMention.work_id, RawWorkMention.candidate_work_id).where(
            or_(RawWorkMention.work_id.in_(chunk), RawWorkMention.candidate_work_id.in_(chunk))
        )
    ).tuples()
    moved = [
        {
            "id": mention_id,
            "work_id": merges.get(work_id, work_id) if work_id is not None else None,
            "candidate_work_id": merges.get(candidate, candidate) if candidate is not None else None,
        }
        for mention_id, work_id, candidate in mentions
    ]
    if moved:
        session.execute(update(RawWorkMention), moved)

    targets = list({merges[source] for source in chunk})
    for model, columns in (
        (WorkTitle, (WorkTitle.title_key, WorkTitle.source_id)),
        (WorkIdentifier, (WorkIdentifier.scheme, WorkIdentifier.value, WorkIdentifier.source_id)),
    ):
        kept: set[tuple[object, ...]] = {
            tuple(row)
            for row in session.execute(select(model.work_id, *columns).where(model.work_id.in_(targets)))
        }
        drop: list[int] = []
        move: list[dict[str, object]] = []
        for row in session.execute(select(model.id, model.work_id, *columns).where(model.work_id.in_(chunk))):
            row_id, source, *key = row
            target = merges[source]
            identity: tuple[object, ...] = (target, *key)
            if identity in kept:
                drop.append(row_id)
            else:
                kept.add(identity)
                move.append({"id": row_id, "work_id": target})
        if drop:
            session.execute(delete(model).where(model.id.in_(drop)))
        if move:
            session.execute(update(model), move)

    session.execute(
        delete(WorkLink).where(or_(WorkLink.work_id.in_(chunk), WorkLink.target_work_id.in_(chunk)))
    )


def _fold_works(session: Session, merges: dict[uuid.UUID, uuid.UUID], chunk: list[uuid.UUID]) -> None:
    """Carry each folded work's premiere and ingest times onto its target, then
    delete it."""
    works = {
        work.id: work
        for work in session.scalars(select(Work).where(Work.id.in_([*chunk, *{merges[s] for s in chunk}])))
    }
    for source in chunk:
        old, new = works[source], works[merges[source]]
        if old.premiere_date is not None and supersedes(old.premiere_date, new.premiere_date):
            new.premiere_date = old.premiere_date
            new.premiere_place = old.premiere_place
            new.premiere_source_id = old.premiere_source_id
        new.first_ingested_at = min(new.first_ingested_at, old.first_ingested_at)
        new.last_ingested_at = max(new.last_ingested_at, old.last_ingested_at)
    session.flush()
    session.execute(delete(Work).where(Work.id.in_(chunk)))


def _resolve(merges: dict[uuid.UUID, uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """Each folded work's final target: a later round can fold a target too."""
    resolved: dict[uuid.UUID, uuid.UUID] = {}
    for source, target in merges.items():
        while target in merges:
            target = merges[target]
        resolved[source] = target
    return resolved


def _apply(session: Session, merges: dict[uuid.UUID, uuid.UUID]) -> None:
    """Fold every work in ``merges`` into its (final) target."""
    merges = _resolve(merges)
    sources = sorted(merges, key=str)
    for start in range(0, len(sources), CHUNK):
        chunk = sources[start : start + CHUNK]
        _move_rows(session, merges, chunk)
        _fold_works(session, merges, chunk)


def merge_works(session: Session) -> MergeStats:
    """Fold the works split across a composer's duplicate entities, and the
    composerless works whose catalogue number names their composer; commits."""
    canonical = composer_canonicals(session)
    groups: dict[uuid.UUID, dict[uuid.UUID, list[Candidate]]] = {}
    orphans: list[Candidate] = []
    for work_id, composer_id, features in candidate_rows(session):
        if composer_id is None:
            orphans.append(Candidate(work_id, features))
        else:
            key = canonical.get(composer_id, composer_id)
            groups.setdefault(key, {}).setdefault(composer_id, []).append(Candidate(work_id, features))

    merges: dict[uuid.UUID, uuid.UUID] = {}
    pools = {key: _fold_group(key, by_composer, merges) for key, by_composer in groups.items()}
    within = len(merges)
    composerless = _fold_composerless(orphans, pools, merges)

    _apply(session, merges)
    session.commit()
    session.expire_all()
    return MergeStats(within_composer=within, composerless=composerless)
