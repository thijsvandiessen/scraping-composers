"""Point parts, arrangements and versions at the work they derive from.

The matcher keeps a derived work apart from its parent (see
:mod:`.match`): "Allegro (from Brandenburg Concerto No. 3, BWV 1048)" is a
movement, not the concerto, though it carries the concerto's number. This pass
records the relation instead, as ``work_links`` rows, so concert and recording
counts can roll up to the parent:

- **catalogue** -- a derived work points at the one non-derived work by the
  same composer that holds one of its parent catalogue numbers. Two candidates
  is ambiguous and links neither.
- **catalogue_range** -- a work with a single number points (``part_of``) at
  the smallest work by the same composer whose range or list holds it:
  *Invention No. 1, BWV 772* at *15 Inventions, BWV 772-786*, not at the
  *Inventions and Sinfonias, BWV 772-801* that set sits inside. Only sets
  that overlap without nesting (``BWV 772-786`` and ``BWV 780-801``), or two
  works holding the same set, leave the number ambiguous, and it links to
  neither.

"The same composer" is the canonical one: the works of every entity a
composer is filed under (``dedupe_persons``) form one catalogue, as they do
for the matcher (:class:`~composer_warehouse.works.WorkCandidates`).
- **wikidata** -- a Wikidata work that is *part of* (P361) another item points
  at the work carrying that item's QID.

The table is deleted and rebuilt on every run. Work ids are random, so nothing
about a previous run's rows is worth keeping, and the result depends only on
the works and their identifiers, never on the order they were created in.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass

from composer_models import RawWorkMention, Source, WorkIdentifier, WorkLink
from composer_schema.catalogues import catalogue_members, is_catalogue_set
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import Candidate, load_candidates
from .schemes import holds_sets
from .sources import part_of

WIKIDATA_PART_SOURCE = "wikidata_catalogue"


@dataclass(frozen=True)
class LinkStats:
    links: int = 0
    by_catalogue: int = 0
    by_range: int = 0
    by_wikidata: int = 0
    # derived works whose parent number fits more than one work, and works
    # whose number falls inside sets that overlap without nesting
    ambiguous: int = 0


def _catalogue_links(session: Session) -> tuple[dict[tuple[uuid.UUID, str, uuid.UUID], str], int]:
    links: dict[tuple[uuid.UUID, str, uuid.UUID], str] = {}
    ambiguous = 0
    for candidates in load_candidates(session).groups.values():
        holders: dict[tuple[str, str], set[uuid.UUID]] = {}
        for cand in candidates:
            if cand.features.derivation is None:
                for identifier in cand.features.identifiers:
                    holders.setdefault(identifier, set()).add(cand.work_id)
        for cand in candidates:
            if cand.features.derivation is None:
                continue
            targets = {
                work_id
                for identifier in cand.features.parent_identifiers
                for work_id in holders.get(identifier, ())
            }
            if len(targets) == 1:
                links[(cand.work_id, f"{cand.features.derivation}_of", targets.pop())] = "catalogue"
            elif targets:
                ambiguous += 1
        ambiguous += _range_links(candidates, links)
    return links, ambiguous


#: A set work's members, scheme-qualified so two schemes' numbers never meet.
Members = frozenset[tuple[str, str]]


def _smallest_set(containing: dict[uuid.UUID, Members]) -> uuid.UUID | None:
    """The one set inside every other set holding a number, if there is one.

    Nested sets (a sub-collection inside its collection) give their smallest;
    sets that overlap without nesting, or two works holding the same set, give
    None.
    """
    smallest = min(containing.items(), key=lambda item: len(item[1]))
    work_id, members = smallest
    others = [other for other_id, other in containing.items() if other_id != work_id]
    if all(members < other for other in others):
        return work_id
    return None


def _keep_smaller(holders: dict[uuid.UUID, Members], work_id: uuid.UUID, members: Members) -> None:
    """Note ``work_id`` as holding a number through ``members``; a work stating
    two sets that hold it keeps the smaller."""
    if work_id not in holders or len(members) < len(holders[work_id]):
        holders[work_id] = members


def _set_holders(candidates: list[Candidate]) -> dict[tuple[str, str], dict[uuid.UUID, Members]]:
    """Each single number, with the set works holding it and their members."""
    sets: dict[tuple[str, str], dict[uuid.UUID, Members]] = {}
    for cand in candidates:
        if cand.features.derivation is not None:
            continue
        for scheme, value in cand.features.identifiers:
            if holds_sets(scheme) and is_catalogue_set(value):
                members = frozenset((scheme, member) for member in catalogue_members(value))
                if len(members) > 1:
                    for key in members:
                        _keep_smaller(sets.setdefault(key, {}), cand.work_id, members)
    return sets


def _range_links(candidates: list[Candidate], links: dict[tuple[uuid.UUID, str, uuid.UUID], str]) -> int:
    """Add a ``part_of`` link from each of one composer's single-numbered
    works to the smallest set work holding its number; the count left
    ambiguous."""
    sets = _set_holders(candidates)
    ambiguous = 0
    for cand in candidates:
        if cand.features.derivation is not None:
            continue
        containing: dict[uuid.UUID, Members] = {}
        for identifier in cand.features.identifiers:
            if not is_catalogue_set(identifier[1]):
                for work_id, members in sets.get(identifier, {}).items():
                    if work_id != cand.work_id:
                        _keep_smaller(containing, work_id, members)
        if not containing:
            continue
        if (target := _smallest_set(containing)) is not None:
            links[(cand.work_id, "part_of", target)] = "catalogue_range"
        else:
            ambiguous += 1
    return ambiguous


def _wikidata_links(session: Session) -> dict[tuple[uuid.UUID, str, uuid.UUID], str]:
    by_qid: dict[str, set[uuid.UUID]] = {}
    for work_id, qid in session.execute(
        select(WorkIdentifier.work_id, WorkIdentifier.value).where(WorkIdentifier.scheme == "wikidata")
    ).tuples():
        by_qid.setdefault(qid, set()).add(work_id)

    links: dict[tuple[uuid.UUID, str, uuid.UUID], str] = {}
    for work_id, raw in session.execute(
        select(RawWorkMention.work_id, RawWorkMention.raw)
        .join(Source, Source.id == RawWorkMention.source_id)
        .where(Source.name == WIKIDATA_PART_SOURCE, RawWorkMention.work_id.is_not(None))
    ).tuples():
        if work_id is None:
            continue
        for qid in part_of(WIKIDATA_PART_SOURCE, json.loads(raw)):
            targets = by_qid.get(qid, set()) - {work_id}
            if len(targets) == 1:
                links[(work_id, "part_of", targets.pop())] = "wikidata"
    return links


def link_works(session: Session) -> LinkStats:
    """Delete and rebuild ``work_links``; commits."""
    session.execute(delete(WorkLink))
    links, ambiguous = _catalogue_links(session)
    for key, method in _wikidata_links(session).items():
        links.setdefault(key, method)

    for (work_id, relation, target_id), method in sorted(links.items(), key=lambda item: str(item[0])):
        session.add(WorkLink(work_id=work_id, relation=relation, target_work_id=target_id, method=method))
    session.commit()

    methods = Counter(links.values())
    return LinkStats(
        links=len(links),
        by_catalogue=methods["catalogue"],
        by_range=methods["catalogue_range"],
        by_wikidata=methods["wikidata"],
        ambiguous=ambiguous,
    )
