"""Cannot-link constraints derived from authority identifiers.

Wikidata QIDs and MusicBrainz ids are *authority* identifiers: two distinct
ones are two distinct items, and usually two distinct people. The warehouse has
had that evidence all along and the dedupe pass threw it away at decision time
— it trains on ``distinct_musicbrainz`` as a negative label (see
:mod:`evaluation`) and then let 862 canonical clusters span two or more QIDs
on the 2026-08-25 database.

A distinct QID is strong evidence, not proof. Wikidata holds duplicate items,
and some of the merges the pass made are right::

    aaron aachen|Q139217390   born 1933          no aliases
    norbert linke|Q1796591    born 1933-03-05    alias: 'Aaron Aachen'

Wikidata itself records "Aaron Aachen" as Linke's pseudonym and the birth years
agree, so this is one person filed twice. The rule therefore cannot be "never
cross a QID boundary"; it is a constraint that corroboration can discharge —
the two items must agree on a birth year *and* one must name the other in
``also_known_as``. Both halves matter: agreement on a year alone is what two
different people born the same year also look like, and wikidata naming the
other item's name is wikidata conceding the link.

What survives is fed to :func:`~.cluster.build_clusters` as a hard cannot-link,
so the merge is refused even when it arrives transitively through a third
record — which is how most of the 862 were reached. Refusals are returned
rather than dropped: a constraint that fires on a high-scoring pair is a signal
about the model, not just about the pair.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

from .cluster import Edge, build_clusters
from .corpus import PersonRecord, alias_identity

WIKIDATA = "wikidata"
MUSICBRAINZ = "musicbrainz"

# Birth years this close agree: sources routinely disagree by a year, and a
# corroborated duplicate should not turn on which of them was read first.
# Deliberately its own constant rather than ``evaluation.YEAR_TOLERANCE`` —
# that one decides a training label, this one refuses a merge, and the two are
# free to move apart.
BIRTH_YEAR_TOLERANCE = 1


@dataclass(frozen=True)
class Conflict:
    """Two records whose authority ids disagree about being the same person.

    ``authorities`` names the ones that disagree — both, when the pair carries
    a distinct QID *and* a distinct MusicBrainz id. ``corroborated`` is the
    discharge: true means the constraint is waived and the pair may merge.
    """

    a: uuid.UUID
    b: uuid.UUID
    authorities: tuple[str, ...]
    corroborated: bool


@dataclass(frozen=True)
class Constraints:
    """Every authority conflict found, discharged or not."""

    conflicts: tuple[Conflict, ...] = ()

    @property
    def cannot_link(self) -> tuple[tuple[uuid.UUID, uuid.UUID], ...]:
        """The pairs :func:`~.cluster.build_clusters` must keep apart."""
        return tuple((c.a, c.b) for c in self.conflicts if not c.corroborated)

    @property
    def discharged(self) -> tuple[Conflict, ...]:
        """Conflicts corroboration waived — the wikidata duplicates we keep."""
        return tuple(c for c in self.conflicts if c.corroborated)


def _distinct(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether both sides carry an id from one authority and none in common."""
    return bool(a and b and not (a & b))


def _authorities_in_conflict(a: PersonRecord, b: PersonRecord) -> tuple[str, ...]:
    found: list[str] = []
    if _distinct(a.wikidata_ids, b.wikidata_ids):
        found.append(WIKIDATA)
    if _distinct(a.musicbrainz_ids, b.musicbrainz_ids):
        found.append(MUSICBRAINZ)
    return tuple(found)


def corroborated(a: PersonRecord, b: PersonRecord) -> bool:
    """Whether two conflicting records are one person after all.

    Agreement on a birth year *and* one item naming the other's name in
    ``also_known_as``. The alias half is the load-bearing one: it is the
    authority contradicting its own id, which is the only thing strong enough
    to overrule that id.
    """
    if a.birth_year is None or b.birth_year is None:
        return False
    if abs(a.birth_year - b.birth_year) > BIRTH_YEAR_TOLERANCE:
        return False
    return alias_identity(a, b)


def authority_constraints(records: Sequence[PersonRecord], edges: Iterable[Edge]) -> Constraints:
    """Find the authority conflicts among the records ``edges`` could merge.

    Enumerating all 130,862 QID-carrying records pairwise would be 8.6 billion
    comparisons for a constraint that can only ever bind two records the edges
    already connect. So the search is restricted to the connected components of
    the unconstrained edge graph: a constrained clustering only merges subsets
    of those, which makes the components an exact — and tiny — bound on where a
    conflict can matter.
    """
    by_id = {record.entity_id: record for record in records}
    conflicts: list[Conflict] = []
    for component in build_clusters(edges).clusters:
        # Sorted so the conflict list is a function of the input, not of set
        # iteration order, and two runs report the same pairs in the same order.
        for a, b in combinations(sorted(component), 2):
            ra, rb = by_id.get(a), by_id.get(b)
            if ra is None or rb is None:
                continue
            if authorities := _authorities_in_conflict(ra, rb):
                conflicts.append(Conflict(a, b, authorities, corroborated(ra, rb)))
    return Constraints(tuple(conflicts))
