"""Fold a source's page-scoped recordings into one cluster per release.

``derive_recordings`` keys recordings by the LLM's ``record_key``, which embeds
the page url — so an album listed on its own review page *and* on five tag pages
arrives as six recordings. This pass gives them a content identity instead: rows
are blocked by (source, normalized title) and linked when they share a
performer, unless they carry conflicting catalogue numbers, which mark distinct
releases.

Blocking on the title is what keeps genuinely different albums apart. A "top 10
Bach cello suites" round-up yields ten same-titled recordings that share no
performer, so none of them merge.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..normalize import dedup_key

# The recording key ``_group_recordings`` folds mentions into: (source, record key).
Key = tuple[int, str]

# Dropped from a performer's key so "Sir Simon Rattle" and "Simon Rattle" are one
# credit. Local to recordings on purpose: the shared ``dedup_key`` seeds every
# entity id, so it must keep treating the two spellings as written.
_HONORIFICS = frozenset({"sir", "dame", "lord", "lady", "maestro", "maestra"})

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


def participant_key(name: str) -> str:
    """A performer's identity for clustering and credit dedup: the shared
    ``dedup_key`` with any leading honorifics stripped."""
    tokens = dedup_key(name).split()
    while tokens and tokens[0] in _HONORIFICS:
        tokens = tokens[1:]
    return " ".join(tokens)


def catalogue_key(value: str | None) -> str | None:
    """A label's release id, case- and punctuation-folded ('BIS-2476' -> 'bis2476')."""
    if not value:
        return None
    return _NON_ALPHANUMERIC.sub("", value.lower()) or None


class _Clusters:
    """Union-find over recording keys that refuses to put two different
    catalogue numbers in one cluster — those are separate releases."""

    def __init__(self, catalogues: dict[Key, str | None]) -> None:
        self._parent: dict[Key, Key] = {key: key for key in catalogues}
        self._catalogues: dict[Key, set[str]] = {
            key: {value} if value else set() for key, value in catalogues.items()
        }

    def _find(self, key: Key) -> Key:
        while self._parent[key] != key:
            self._parent[key] = self._parent[self._parent[key]]
            key = self._parent[key]
        return key

    def union(self, a: Key, b: Key) -> None:
        root_a, root_b = self._find(a), self._find(b)
        if root_a == root_b:
            return
        catalogues = self._catalogues[root_a] | self._catalogues[root_b]
        if len(catalogues) > 1:
            return
        self._parent[root_b] = root_a
        self._catalogues[root_a] = catalogues

    def groups(self) -> list[list[Key]]:
        """Every key exactly once, grouped by cluster, in a deterministic order."""
        members: dict[Key, list[Key]] = defaultdict(list)
        for key in sorted(self._parent):
            members[self._find(key)].append(key)
        return [members[root] for root in sorted(members)]


def cluster_recordings(grouped: dict[Key, dict[str, Any]]) -> list[list[Key]]:
    """Group page-scoped recordings into clusters, one per release.

    Rows are only ever compared inside a (source, normalized title) block, and
    are linked when they share a performer. Untitled rows stay on their own.
    """
    clusters = _Clusters({key: catalogue_key(data["catalogue_number"]) for key, data in grouped.items()})

    blocks: dict[tuple[int, str], list[Key]] = defaultdict(list)
    for key in sorted(grouped):
        title = grouped[key]["title"]
        if title:
            blocks[(key[0], dedup_key(title))].append(key)

    for block in blocks.values():
        by_performer: dict[str, list[Key]] = defaultdict(list)
        for key in block:
            for name in grouped[key]["participants"]:
                performer = participant_key(name)
                if performer:
                    by_performer[performer].append(key)
        for members in by_performer.values():
            for other in members[1:]:
                clusters.union(members[0], other)

    return clusters.groups()
