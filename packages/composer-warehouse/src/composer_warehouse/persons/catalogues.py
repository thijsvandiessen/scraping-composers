"""Catalogue evidence: one composer's numbers held by two of its entities (#362).

A Köchel number is Mozart's. When the surname-only "Mozart" holds *Symphony
No. 40, K. 550* and "Wolfgang Amadeus Mozart" holds it too, the two entities
are one composer -- and the name model cannot tell, because a bare surname
carries no given names or dates to compare (#202). So a number shared in a
composer-scoped catalogue (:func:`~composer_warehouse.works.schemes.is_composer_scoped`)
is turned into a link between the two entities' clusters.

A shared number is strong evidence, not proof: an arrangement is filed under
its arranger, a work is misattributed, a label is a family name. So the link
is conservative, and every rule is about the *names* the number is shared
between, never a hand-made list:

- **Compatible names.** The two clusters must share a surname, and no pair
  across them may contradict: a given name that conflicts on that surname
  ("Leopold" against "Wolfgang Amadeus") or a birth or death year more than
  :data:`YEAR_TOLERANCE` apart. "Mozart" and "Wolfgang Amadeus Mozart" pass;
  "Johann Nepomuk Hummel", who holds a KV number by arranging it, does not.
- **No rival.** A cluster whose partners include two that are incompatible
  with each other ("Mozart" sharing numbers with both Wolfgang Amadeus and
  Leopold) links to neither, unless the stronger partner shares more than
  twice the numbers -- a surname-only entity goes with the composer whose
  catalogue it overwhelmingly holds, or with no one.
- **Constraints still bind.** The links are clustered with the incompatible
  pairs, human rejections and authority conflicts as cannot-links, strongest
  (most numbers shared) first, so two different people are never joined
  through a chain of them either.

What survives is recorded as an ``auto_linked`` ``PersonMatch`` with method
``shared_catalogue:<count>`` and joins the partition like any other link.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import combinations

from composer_models import Work, WorkIdentifier
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..works.schemes import is_composer_scoped
from .cluster import Edge, build_clusters
from .compare import GivenLevel, given_level
from .corpus import PersonRecord

METHOD = "shared_catalogue"

#: The score recorded for a catalogue link: above the auto threshold, so it is
#: a link, and below a perfect name match, which merges first.
SCORE = 0.995

#: Years further apart than this are two people, whatever they share.
YEAR_TOLERANCE = 2

#: A number held by more clusters than this is a common arrangement, not
#: evidence about any one pair (and would cost quadratic pairs).
MAX_HOLDERS = 50

#: How many times the numbers a rival shares the stronger partner must share.
DOMINANCE = 2


def composer_keys(session: Session) -> dict[uuid.UUID, set[tuple[str, str]]]:
    """Every composer entity's numbers in composer-scoped catalogues."""
    keys: dict[uuid.UUID, set[tuple[str, str]]] = {}
    for composer_id, scheme, value in session.execute(
        select(Work.composer_entity_id, WorkIdentifier.scheme, WorkIdentifier.value)
        .join(Work, Work.id == WorkIdentifier.work_id)
        .where(Work.composer_entity_id.is_not(None))
        .distinct()
    ).tuples():
        if composer_id is not None and is_composer_scoped(scheme):
            keys.setdefault(composer_id, set()).add((scheme, value))
    return keys


def _years_agree(a: int | None, b: int | None) -> bool:
    return a is None or b is None or abs(a - b) <= YEAR_TOLERANCE


def compatible(a: Sequence[PersonRecord], b: Sequence[PersonRecord]) -> bool:
    """Whether two clusters can be one person: a surname in common and no pair
    across them contradicting on a given name or a year."""
    shared_surname = False
    for x in a:
        for y in b:
            if not (_years_agree(x.birth_year, y.birth_year) and _years_agree(x.death_year, y.death_year)):
                return False
            if x.name.surname and x.name.surname == y.name.surname:
                shared_surname = True
                if given_level(x.name, y.name) is GivenLevel.CONFLICT:
                    return False
    return shared_surname


class _Clusters:
    """The provisional partition: each entity's cluster, named by its least id."""

    def __init__(
        self,
        records: Sequence[PersonRecord],
        edges: Iterable[Edge],
        barred: Iterable[tuple[uuid.UUID, uuid.UUID]],
    ):
        self.root: dict[uuid.UUID, uuid.UUID] = {}
        self.members: dict[uuid.UUID, list[PersonRecord]] = {}
        by_id = {record.entity_id: record for record in records}
        for cluster in build_clusters(edges, barred).clusters:
            name = min(cluster, key=str)
            for member in cluster:
                self.root[member] = name
            self.members[name] = [by_id[m] for m in sorted(cluster, key=str) if m in by_id]
        for record in records:
            if record.entity_id not in self.root:
                self.root[record.entity_id] = record.entity_id
                self.members[record.entity_id] = [record]

    def of(self, entity_id: uuid.UUID) -> uuid.UUID:
        return self.root.get(entity_id, entity_id)


def _shared_counts(
    keys: dict[uuid.UUID, set[tuple[str, str]]], clusters: _Clusters
) -> tuple[Counter[tuple[uuid.UUID, uuid.UUID]], dict[uuid.UUID, Counter[uuid.UUID]]]:
    """Numbers shared per pair of clusters, and each cluster's holders by how
    many numbers they hold."""
    by_key: dict[tuple[str, str], set[uuid.UUID]] = {}
    holders: dict[uuid.UUID, Counter[uuid.UUID]] = {}
    for entity_id, held in keys.items():
        root = clusters.of(entity_id)
        if root not in clusters.members:
            continue  # not a person record
        holders.setdefault(root, Counter())[entity_id] += len(held)
        for key in held:
            by_key.setdefault(key, set()).add(root)
    shared: Counter[tuple[uuid.UUID, uuid.UUID]] = Counter()
    for roots in by_key.values():
        if 1 < len(roots) <= MAX_HOLDERS:
            for a, b in combinations(sorted(roots, key=str), 2):
                shared[(a, b)] += 1
    return shared, holders


def catalogue_links(
    records: Sequence[PersonRecord],
    keys: dict[uuid.UUID, set[tuple[str, str]]],
    links: Iterable[Edge] = (),
    cannot_link: Iterable[tuple[uuid.UUID, uuid.UUID]] = (),
) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """The pairs of entities catalogue evidence links: ``(a, b, numbers shared)``.

    ``links`` and ``cannot_link`` are the partition so far (the scored links,
    the human rejections and authority conflicts), so the evidence is weighed
    between clusters rather than between the entities that happen to hold the
    numbers. Each link joins the two clusters' biggest holders.
    """
    barred = list(cannot_link)
    clusters = _Clusters(records, links, barred)
    shared, holders = _shared_counts(keys, clusters)

    fits: dict[tuple[uuid.UUID, uuid.UUID], bool] = {}

    def fit(a: uuid.UUID, b: uuid.UUID) -> bool:
        pair = (a, b) if str(a) < str(b) else (b, a)
        if pair not in fits:
            fits[pair] = compatible(clusters.members[a], clusters.members[b])
        return fits[pair]

    partners: dict[uuid.UUID, Counter[uuid.UUID]] = {}
    for (a, b), count in shared.items():
        if fit(a, b):
            partners.setdefault(a, Counter())[b] = count
            partners.setdefault(b, Counter())[a] = count

    ambiguous: set[uuid.UUID] = set()
    for root, counts in partners.items():
        ranked = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
        best, best_count = ranked[0]
        if any(not fit(best, other) and DOMINANCE * count >= best_count for other, count in ranked[1:]):
            ambiguous.add(root)

    edges = [
        Edge(a, b, float(count))
        for (a, b), count in shared.items()
        if a not in ambiguous and b not in ambiguous and fit(a, b)
    ]
    roots = {node for edge in edges for node in (edge.a, edge.b)}
    refuse = [(a, b) for a, b in combinations(sorted(roots, key=str), 2) if not fit(a, b)]
    refuse += [(clusters.of(a), clusters.of(b)) for a, b in barred if clusters.of(a) != clusters.of(b)]
    refused = set(build_clusters(edges, refuse).refused)

    def holder(root: uuid.UUID) -> uuid.UUID:
        return min(holders[root].items(), key=lambda item: (-item[1], str(item[0])))[0]

    return [
        (holder(edge.a), holder(edge.b), int(edge.score))
        for edge in sorted(edges, key=lambda e: (-e.score, str(e.a), str(e.b)))
        if edge not in refused
    ]
