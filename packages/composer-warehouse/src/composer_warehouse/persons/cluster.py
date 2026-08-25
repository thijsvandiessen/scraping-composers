"""Turn scored pairs into duplicate clusters.

The dedupe pass decides pairs, but a duplicate is not a pair — it is a group,
and the group is what gold promotes. Recording only pairs left the group
implicit in a chain of ``canonical_entity_id`` pointers, which nothing kept
consistent: ``A -> B`` and ``B -> C`` were two independent decisions, so the
graph could hold chains (453 of them on the 2026-08-25 database) and, in
principle, cycles.

This module builds the partition explicitly. Edges are merged strongest first,
so when a constraint refuses a merge it is the weakest evidence that gets
dropped, and the result is a set of disjoint clusters by construction — no
chains to walk, nowhere for a cycle to hide.

``cannot_link`` is how a group says "these two are not the same person". A
pairwise pass had nowhere to put such a fact: it can decline the pair ``A~B``,
but it cannot stop ``A~C`` and ``C~B`` from merging ``A`` with ``B`` anyway.
Here the constraint is checked against the whole cluster, so a transitive merge
is refused too. Two producers: a human ``rejected`` review row, and
:mod:`constraints`, which derives them from Wikidata and MusicBrainz ids.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Edge:
    """One scored pair. ``score`` orders the merges, strongest first."""

    a: uuid.UUID
    b: uuid.UUID
    score: float


@dataclass(frozen=True)
class Clustering:
    """The partition, plus what it refused to do.

    ``clusters`` holds only the groups of two or more; a record no edge
    survived for is in no cluster and stays its own canonical.
    """

    clusters: tuple[frozenset[uuid.UUID], ...]
    refused: tuple[Edge, ...] = ()

    @property
    def largest(self) -> int:
        return max((len(c) for c in self.clusters), default=0)

    @property
    def members(self) -> int:
        return sum(len(c) for c in self.clusters)


@dataclass
class _Partition:
    """Union-find that also carries each cluster's membership and the records
    it may not absorb, so a cannot-link is enforced against the whole group
    rather than the pair that happened to be scored."""

    parent: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict[uuid.UUID, uuid.UUID])
    members: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict[uuid.UUID, set[uuid.UUID]])
    barred: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict[uuid.UUID, set[uuid.UUID]])

    def add(self, node: uuid.UUID, barred: Iterable[uuid.UUID] = ()) -> None:
        if node not in self.parent:
            self.parent[node] = node
            self.members[node] = {node}
            self.barred[node] = set(barred)

    def find(self, node: uuid.UUID) -> uuid.UUID:
        self.add(node)
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != root:  # path compression
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, a: uuid.UUID, b: uuid.UUID) -> bool:
        """Merge the clusters of ``a`` and ``b``. False if a constraint refuses.

        Merging the smaller cluster into the larger keeps the tree shallow and,
        with path compression, the whole pass near-linear.
        """
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if self.barred[ra] & self.members[rb] or self.barred[rb] & self.members[ra]:
            return False
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.members[ra] |= self.members.pop(rb)
        self.barred[ra] |= self.barred.pop(rb)
        return True

    def groups(self) -> tuple[frozenset[uuid.UUID], ...]:
        return tuple(frozenset(m) for m in self.members.values() if len(m) > 1)


def build_clusters(
    edges: Iterable[Edge], cannot_link: Iterable[tuple[uuid.UUID, uuid.UUID]] = ()
) -> Clustering:
    """Partition the entities joined by ``edges``, honouring ``cannot_link``.

    Edges are applied in descending score order — ties broken on the ids, so
    the partition is a function of the input and not of iteration order.
    """
    partition = _Partition()
    barred: dict[uuid.UUID, set[uuid.UUID]] = {}
    for a, b in cannot_link:
        barred.setdefault(a, set()).add(b)
        barred.setdefault(b, set()).add(a)
    for node, others in barred.items():
        partition.add(node, others)

    refused: list[Edge] = []
    for edge in sorted(edges, key=lambda e: (-e.score, str(e.a), str(e.b))):
        if not partition.union(edge.a, edge.b):
            refused.append(edge)
    return Clustering(clusters=partition.groups(), refused=tuple(refused))
