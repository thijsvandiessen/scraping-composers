"""Load person records out of the warehouse and block them into candidates.

Shared by the three things that need the same view of the data: the dedupe
pass, EM training, and the evaluation-set builder. Keeping it in one place
means the model is trained on exactly the pairs it is later asked to score.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from composer_models import Claim, Entity
from composer_models.normalize import wikidata_id
from sqlalchemy import select
from sqlalchemy.orm import Session

from .extract import REORDERED, PersonName, parse_name
from .fellegi_sunter import TermFrequencyTable
from .match import Corpus, PersonProfile, profile_readings

_YEAR = re.compile(r"\d{4}")

# Blocks above this size are dominated by a handful of very common surnames and
# contribute O(n^2) pairs of almost entirely non-matches. They are still scored
# — the term-frequency adjustment is what makes them tractable — but the cap
# keeps a pathological group (a mis-parsed token shared by thousands of rows)
# from stalling the pass.
MAX_BLOCK = 2000


@dataclass
class PersonRecord:
    """One person entity, parsed and annotated with its corroborating facts."""

    entity_id: uuid.UUID
    label: str
    name: PersonName
    aliases: tuple[PersonName, ...] = ()
    birth_year: int | None = None
    death_year: int | None = None
    birth_sources: frozenset[int] = frozenset()
    death_sources: frozenset[int] = frozenset()
    musicbrainz_ids: frozenset[str] = frozenset()
    # An entity carries at most one QID — it is part of its dedup key — but the
    # set shape lets :mod:`constraints` compare both authorities the same way.
    wikidata_ids: frozenset[str] = frozenset()
    sources: frozenset[int] = frozenset()
    birth_years: frozenset[int] = field(default_factory=frozenset[int])
    death_years: frozenset[int] = field(default_factory=frozenset[int])

    def profile(self) -> PersonProfile:
        return PersonProfile(
            name=self.name,
            birth_year=self.birth_year,
            aliases=self.aliases,
            death_year=self.death_year,
        )

    def surnames(self) -> set[str]:
        """Every surname this record is written under (primary + aliases).

        Alternate readings are left out: they are guesses at how a label might
        be meant, not surnames anyone wrote, so they don't count towards how
        common a surname is (see :func:`build_corpus`).
        """
        found = {self.name.surname}
        found.update(alias.surname for alias in self.aliases)
        return {surname for surname in found if surname}

    def block_keys(self) -> dict[str, bool]:
        """Every key this record is blocked under, mapped to whether it holds
        the key as written (``True``) or only read surname-first (``False``).

        A surname key comes from every reading that keeps the surname where it
        was written: the primary parse, an alias, a run-together surname. A
        surname-first reading gets a narrower key instead, its surname plus its
        given name (:func:`_reordered_key`), and every written reading gets
        that key too so the two can meet. The keys mirror
        :func:`~.extract.comparable` exactly: a pair blocked only under such a
        key is one the scorer will actually compare.

        That narrowing is what keeps the extra keys cheap. Under the surname
        alone, "Thomas Mann" read surname-first lands beside the 277 people
        actually surnamed Thomas, and crossing every such reading with every
        holder of the surname doubled the candidate pairs; only "Mann Thomas"
        could be him.
        """
        keys: dict[str, bool] = {}
        for reading in profile_readings(self.profile()):
            if not reading.surname:
                continue
            written = reading.reading != REORDERED
            if written:
                keys[reading.surname] = True
            if reading.given:
                key = _reordered_key(reading)
                keys[key] = keys.get(key, False) or written
        return keys


def _reordered_key(name: PersonName) -> str:
    """The block a surname-first reading is paired in. ``|`` cannot occur in a
    tokenised name, so these never collide with a surname key."""
    return f"{name.surname}|{name.given[0]}"


def _year(value: str) -> int | None:
    found = _YEAR.search(value)
    return int(found.group()) if found else None


@dataclass
class _ClaimIndex:
    years: dict[str, dict[uuid.UUID, dict[int, int]]]
    aliases: dict[uuid.UUID, list[PersonName]]
    musicbrainz: dict[uuid.UUID, set[str]]


def _load_claims(session: Session) -> _ClaimIndex:
    """One pass over the claims that bear on person identity."""
    years: dict[str, dict[uuid.UUID, dict[int, int]]] = {
        "born_on": defaultdict(dict),
        "died_on": defaultdict(dict),
    }
    aliases: dict[uuid.UUID, list[PersonName]] = defaultdict(list)
    musicbrainz: dict[uuid.UUID, set[str]] = defaultdict(set)

    wanted = ("born_on", "died_on", "also_known_as", "musicbrainz_id")
    rows = session.execute(
        select(Claim.subject_id, Claim.predicate, Claim.value, Claim.source_id).where(
            Claim.predicate.in_(wanted)
        )
    )
    for subject_id, predicate, value, source_id in rows:
        if not value:
            continue
        if predicate in years:
            if (year := _year(value)) is not None:
                years[predicate][subject_id][source_id] = year
        elif predicate == "also_known_as":
            aliases[subject_id].append(parse_name(value))
        else:
            musicbrainz[subject_id].add(value.strip())
    return _ClaimIndex(years=years, aliases=aliases, musicbrainz=musicbrainz)


def _consensus(by_source: dict[int, int]) -> int | None:
    """The year the most sources agree on, ties broken by the earliest year.

    Sources disagree about dates constantly. Taking the modal value rather than
    the first one seen is what makes "same year, corroborated by three sources"
    beat a single outlying record.
    """
    if not by_source:
        return None
    counts = Counter(by_source.values())
    best = max(counts.values())
    return min(year for year, count in counts.items() if count == best)


def load_records(session: Session, entities: Sequence[Entity] | None = None) -> list[PersonRecord]:
    """Every person entity as a :class:`PersonRecord`."""
    if entities is None:
        entities = list(session.scalars(select(Entity).where(Entity.kind == "person")))
    index = _load_claims(session)
    entity_sources: dict[uuid.UUID, set[int]] = defaultdict(set)
    for entity_id, source_id in session.execute(
        select(Entity.id, Claim.source_id).join(Claim, Claim.subject_id == Entity.id)
    ):
        entity_sources[entity_id].add(source_id)

    records: list[PersonRecord] = []
    for entity in entities:
        born = index.years["born_on"].get(entity.id, {})
        died = index.years["died_on"].get(entity.id, {})
        qid = wikidata_id(entity.dedup_key)
        records.append(
            PersonRecord(
                entity_id=entity.id,
                label=entity.label,
                name=parse_name(entity.label),
                aliases=tuple(index.aliases.get(entity.id, ())),
                birth_year=_consensus(born),
                death_year=_consensus(died),
                birth_sources=frozenset(born),
                death_sources=frozenset(died),
                birth_years=frozenset(born.values()),
                death_years=frozenset(died.values()),
                musicbrainz_ids=frozenset(index.musicbrainz.get(entity.id, ())),
                wikidata_ids=frozenset({qid} if qid else ()),
                sources=frozenset(entity_sources.get(entity.id, ())),
            )
        )
    return records


def given_keys(record: PersonRecord) -> set[str]:
    """Every spelled-out given-name string this record can be compared on."""
    keys = {" ".join(name.given) for name in (record.name, *record.aliases) if name.given}
    return {key for key in keys if key}


def _name_key(name: PersonName) -> tuple[str, tuple[str, ...]]:
    """Identity of a name ignoring word order, so "Bach, Johann" and "Johann
    Bach" are recognised as the same surface name rather than two."""
    return name.surname, tuple(sorted(name.given))


def alias_identity(a: PersonRecord, b: PersonRecord) -> bool:
    """One side's full name is listed as an alias of the other, spelled
    differently.

    Wikidata's own alias curation, which is why it serves two callers: it
    labels a match for :mod:`evaluation`, and it is half of what discharges an
    authority-id cannot-link in :mod:`constraints`.

    Pairs whose names differ only in word order are excluded along with exactly
    equal ones: "Beethoven, Ludwig van" against "Ludwig van Beethoven" is the
    trivial case every scorer gets right, and counting it as ground truth would
    flatter all of them equally.
    """
    if _name_key(a.name) == _name_key(b.name):
        return False
    a_aliases = {alias.normalized for alias in a.aliases}
    b_aliases = {alias.normalized for alias in b.aliases}
    return b.name.normalized in a_aliases or a.name.normalized in b_aliases


def build_corpus(records: Sequence[PersonRecord]) -> Corpus:
    """Term-frequency tables over the surnames and given names in ``records``.

    Each record counts *once per distinct value*, exactly as
    :func:`surname_blocks` places it. Counting once per alias instead inflates
    the totals relative to the block sizes, which deflates every frequency and
    hands every pair a constant positive term-frequency bonus — a bias large
    enough to swamp the comparison weights entirely.
    """
    surnames: Counter[str] = Counter()
    given: Counter[str] = Counter()
    for record in records:
        surnames.update(record.surnames())
        given.update(given_keys(record))
    return Corpus(
        surnames=TermFrequencyTable.from_counts(surnames, max_count=MAX_BLOCK),
        given_names=TermFrequencyTable.from_counts(given, max_count=MAX_BLOCK),
    )


@dataclass
class Block:
    """The records filed under one blocking key, split by how they got there."""

    anchored: list[PersonRecord] = field(default_factory=list[PersonRecord])
    """Records with the key under a reading that keeps the surname in place."""
    reordered: list[PersonRecord] = field(default_factory=list[PersonRecord])
    """Records with the key only when read surname-first."""


def surname_blocks(records: Sequence[PersonRecord]) -> dict[str, Block]:
    """Group records by every key they can be found under (see
    :meth:`PersonRecord.block_keys`)."""
    blocks: dict[str, Block] = defaultdict(Block)
    for record in records:
        for key, anchored in record.block_keys().items():
            block = blocks[key]
            (block.anchored if anchored else block.reordered).append(record)
    return dict(blocks)


def _block_pairs(key: str, block: Block) -> Iterator[tuple[PersonRecord, PersonRecord]]:
    """A surname block pairs its records with each other; a surname-first
    block pairs each reordered record with each anchored one, and nothing else
    (the anchored ones already met in their surname block).

    The cap applies per side. On the anchored side of a surname block it is
    the size :func:`build_corpus` counts the surname at.
    """
    anchored, reordered = block.anchored, block.reordered
    if len(anchored) > MAX_BLOCK or len(reordered) > MAX_BLOCK:
        return
    if "|" not in key:
        for i in range(len(anchored)):
            for j in range(i + 1, len(anchored)):
                yield anchored[i], anchored[j]
        return
    for a in anchored:
        for b in reordered:
            yield a, b


def candidate_pairs(records: Sequence[PersonRecord]) -> Iterator[tuple[PersonRecord, PersonRecord]]:
    """Every pair worth scoring, deduplicated across blocks.

    Blocking on surname is what keeps this from being 22 billion comparisons.
    A pair reachable under two different surnames (via an alias or another
    reading of a name) is yielded once.
    """
    emitted: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for block_key, block in surname_blocks(records).items():
        for a, b in _block_pairs(block_key, block):
            key = (a.entity_id, b.entity_id) if a.entity_id < b.entity_id else (b.entity_id, a.entity_id)
            if key not in emitted:
                emitted.add(key)
                yield a, b
