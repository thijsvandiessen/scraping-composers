"""Anchor person identity on Wikidata rather than on pairwise name matching (#205).

Every Wikidata person entity is keyed on its QID, so it never fuses with
anything at ingest and is a stable identity that already knows the person's
other spellings: its ``also_known_as`` aliases are the variant-spelling
dictionary the pairwise matcher otherwise has to reconstruct from scratch.

So before the surname-blocked pairwise pass, every person entity *without* a
QID is looked up in an index of the QID entities' names: the primary label and
every alias, each under its alternate readings (#201). A record whose names hit
exactly one QID becomes a candidate pair with that item's entity, which the
dedupe pass scores with the ordinary model and links on the ordinary
threshold. This is a candidate generator, not a bypass of the model.

A record whose names hit several QIDs is the "John Williams" case: every one of
them fits it equally well by name, and picking one would be a coin flip. It is
recorded as ambiguous, with the candidate QIDs, and the dedupe pass holds back
every machine link it has, so it stays unlinked until a human decides.

The lookup is exact equality on the parsed surname and given names, which keeps
it conservative: word order and particles don't matter ("Beethoven, Ludwig van"
is "Ludwig van Beethoven"), but initials, nicknames and misspellings are left
for the model to weigh pairwise. A bare surname is never looked up: "Mozart"
is an alias of more than one Mozart, and a name with no given names cannot
tell namesakes apart anyway (#202).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .corpus import PersonRecord
from .match import profile_readings

METHOD = "authority"
"""Prefix of a :class:`~composer_models.PersonMatch` method the anchor pass decided."""

NameKey = tuple[str, tuple[str, ...]]
"""Surname and given names, in order: what the authority index is keyed on."""


def name_keys(record: PersonRecord) -> frozenset[NameKey]:
    """Every key ``record`` is filed or looked up under.

    The record's names under every reading the scorer compares them under
    (:func:`~.match.profile_readings`), so "Xiaogang Ye" read surname-first
    finds an item labelled "Ye Xiaogang". A reading without given names gives
    no key.
    """
    return frozenset(
        (reading.surname, reading.given)
        for reading in profile_readings(record.profile())
        if reading.surname and reading.given
    )


@dataclass(frozen=True)
class AuthorityIndex:
    """The QID entities, findable by any of their names."""

    qids: dict[NameKey, frozenset[str]]
    """The QIDs filed under each name key."""
    entities: dict[str, tuple[PersonRecord, ...]]
    """The entities carrying each QID: usually one, more when Wikidata's label
    changed between runs and the item was keyed under both."""

    @classmethod
    def build(cls, records: Sequence[PersonRecord]) -> AuthorityIndex:
        qids: dict[NameKey, set[str]] = defaultdict(set)
        entities: dict[str, list[PersonRecord]] = defaultdict(list)
        for record in records:
            for qid in record.wikidata_ids:
                entities[qid].append(record)
                for key in name_keys(record):
                    qids[key].add(qid)
        return cls(
            qids={key: frozenset(found) for key, found in qids.items()},
            entities={qid: tuple(found) for qid, found in entities.items()},
        )

    def lookup(self, record: PersonRecord) -> frozenset[str]:
        """Every QID one of ``record``'s names is filed under."""
        found: set[str] = set()
        for key in name_keys(record):
            found.update(self.qids.get(key, ()))
        return frozenset(found)


@dataclass(frozen=True)
class AuthorityCandidates:
    """What the lookup found for the records without a QID."""

    pairs: list[tuple[PersonRecord, PersonRecord, str]]
    """(record, QID entity, QID) for every record that hit exactly one QID."""
    ambiguous: dict[uuid.UUID, frozenset[str]]
    """Records that hit several QIDs, with the QIDs they hit."""
    resolved: int
    """How many records hit exactly one QID."""
    unmatched: int
    """How many records hit none."""


def authority_candidates(records: Sequence[PersonRecord]) -> AuthorityCandidates:
    """Look every record without a QID up in the index of those with one."""
    index = AuthorityIndex.build(records)
    pairs: list[tuple[PersonRecord, PersonRecord, str]] = []
    ambiguous: dict[uuid.UUID, frozenset[str]] = {}
    resolved = unmatched = 0
    for record in records:
        if record.wikidata_ids:
            continue
        qids = index.lookup(record)
        if not qids:
            unmatched += 1
        elif len(qids) > 1:
            ambiguous[record.entity_id] = qids
        else:
            resolved += 1
            (qid,) = qids
            pairs.extend((record, anchor, qid) for anchor in index.entities[qid])
    return AuthorityCandidates(pairs=pairs, ambiguous=ambiguous, resolved=resolved, unmatched=unmatched)


@dataclass(frozen=True)
class Resolution:
    """How the person entities without a QID came out of the partition."""

    authority: int
    """In a cluster with a QID entity: resolved to an authority item, whether
    by the anchor pass or through pairwise links."""
    pairwise: int
    """In a cluster without one: resolved only against other spellings."""
    unresolved: int
    """In no cluster at all."""


def resolution(records: Sequence[PersonRecord], clusters: Sequence[frozenset[uuid.UUID]]) -> Resolution:
    """Count where every record without a QID ended up."""
    anchored = {record.entity_id for record in records if record.wikidata_ids}
    cluster_of = {member: members for members in clusters for member in members}
    authority = pairwise = unresolved = 0
    for record in records:
        if record.wikidata_ids:
            continue
        members = cluster_of.get(record.entity_id)
        if members is None:
            unresolved += 1
        elif members & anchored:
            authority += 1
        else:
            pairwise += 1
    return Resolution(authority=authority, pairwise=pairwise, unresolved=unresolved)


@dataclass(frozen=True)
class AnchorReport:
    """What anchoring on Wikidata did in one dedupe pass."""

    linked: int
    """Links the anchor pass made (counted in the pass's ``auto`` too)."""
    queued: int
    """Pairs it queued for review."""
    ambiguous: int
    """Records whose names hit several QIDs, held back from every machine link."""
    unmatched: int
    """Records whose names hit no QID, left to the pairwise pass."""
    resolution: Resolution
