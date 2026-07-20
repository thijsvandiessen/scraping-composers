"""The post-hoc person dedupe pass.

Scans existing ``person`` entities, groups them by surname, scores every pair in
a group, and records the decision: high-confidence pairs are linked
(``Entity.canonical_entity_id`` set + a ``PersonMatch`` row); middling pairs are
queued for review; the rest are ignored. Re-running is idempotent — a pair that
already has a ``PersonMatch`` is skipped, so the pass can be re-run as the
heuristics improve.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Claim, Entity, PersonMatch
from .extract import PersonName, parse_name
from .match import classify, score

COMMIT_BATCH = 1000
_YEAR = re.compile(r"\d{4}")


def _birth_years(session: Session) -> dict[uuid.UUID, int]:
    """Map person entity id -> birth year, parsed from its first ``born_on`` claim."""
    years: dict[uuid.UUID, int] = {}
    for subject_id, value in session.execute(
        select(Claim.subject_id, Claim.value).where(Claim.predicate == "born_on")
    ).tuples():
        if value and subject_id not in years:
            m = _YEAR.search(value)
            if m:
                years[subject_id] = int(m.group())
    return years


def _aliases(session: Session) -> dict[uuid.UUID, list[PersonName]]:
    """Map person entity id -> list of aliases, parsed from also_known_as claims."""
    aliases: dict[uuid.UUID, list[PersonName]] = defaultdict(list)
    for subject_id, value in session.execute(
        select(Claim.subject_id, Claim.value).where(Claim.predicate == "also_known_as")
    ).tuples():
        if value:
            aliases[subject_id].append(parse_name(value))
    return dict(aliases)


def _given_length(name: PersonName) -> int:
    """Total spelled-out length of the given names — so "Johann Sebastian"
    counts as fuller than the initials "J. S." (same token count)."""
    return sum(len(token) for token in name.given)


def _canonical_and_duplicate(
    a: Entity, b: Entity, parsed: dict[uuid.UUID, PersonName]
) -> tuple[Entity, Entity]:
    """The fuller name (most spelled-out given names; id tie-break) is canonical."""
    la, lb = _given_length(parsed[a.id]), _given_length(parsed[b.id])
    if la != lb:
        return (a, b) if la > lb else (b, a)
    return (a, b) if str(a.id) < str(b.id) else (b, a)


def dedupe_persons(session: Session) -> tuple[int, int]:  # noqa: C901, PLR0912
    """Run the dedupe pass. Returns (auto-linked count, needs-review count)."""
    years = _birth_years(session)
    aliases = _aliases(session)
    persons = list(session.scalars(select(Entity).where(Entity.kind == "person")))
    parsed = {e.id: parse_name(e.label) for e in persons}
    decided: set[tuple[uuid.UUID, uuid.UUID]] = set(
        session.execute(select(PersonMatch.entity_id, PersonMatch.canonical_entity_id)).tuples()
    )
    linked = {e.id for e in persons if e.canonical_entity_id is not None}

    groups: dict[str, set[Entity]] = defaultdict(set)
    for entity in persons:
        surnames = {parsed[entity.id].surname}
        for alias in aliases.get(entity.id, []):
            surnames.add(alias.surname)

        for surname in surnames:
            if surname:  # skip mononyms / empty surnames — nothing to gate on
                groups[surname].add(entity)

    auto = review = pending = 0
    for group_set in groups.values():
        group = list(group_set)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]

                # We might encounter the same pair in multiple surname groups
                if (a.id, b.id) in decided or (b.id, a.id) in decided:
                    continue

                value, method = score(
                    parsed[a.id],
                    parsed[b.id],
                    years.get(a.id),
                    years.get(b.id),
                    aliases.get(a.id),
                    aliases.get(b.id),
                )
                status = classify(value)
                if status == "distinct":
                    continue
                canonical, duplicate = _canonical_and_duplicate(a, b, parsed)
                if (duplicate.id, canonical.id) in decided:
                    continue
                session.add(
                    PersonMatch(
                        entity_id=duplicate.id,
                        canonical_entity_id=canonical.id,
                        score=value,
                        method=method,
                        status=status,
                    )
                )
                decided.add((duplicate.id, canonical.id))
                if status == "auto_linked":
                    if duplicate.id not in linked:
                        duplicate.canonical_entity_id = canonical.id
                        linked.add(duplicate.id)
                    auto += 1
                else:
                    review += 1
                pending += 1
                if pending % COMMIT_BATCH == 0:
                    session.commit()

    session.commit()
    return auto, review
