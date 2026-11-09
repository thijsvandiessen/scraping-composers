"""Score two person records and classify the result.

The scoring itself lives in :mod:`fellegi_sunter`; this module is the domain
wiring — which comparison columns exist for a person, how a pair of parsed
names maps onto their levels, and where the trained parameters come from.

Scores are posterior probabilities of being the same person, so the thresholds
below are calibrated cut-points rather than the bare constants they replaced
(#173): each is the operating point measured against the labelled evaluation
set built by :mod:`evaluation`. Re-derive them with ``composer person-eval``
after retraining.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .compare import SURNAME_ONLY, GivenLevel, YearLevel, given_level, year_level
from .extract import REORDERED, PersonName, comparable
from .fellegi_sunter import Comparison, LinkageModel, TermFrequencyTable

MODEL_PATH = Path(__file__).with_name("model.json")

# Calibrated against tests/data/person_eval_pairs.jsonl.gz — see MODEL.md for
# the precision/recall each operating point buys.
AUTO_THRESHOLD = 0.99
REVIEW_THRESHOLD = 0.50
# A pair on a surname-only given level is never auto-linked, and is queued for
# review from this cut-point rather than REVIEW_THRESHOLD (#202).
SURNAME_ONLY_REVIEW_THRESHOLD = 0.04

GIVEN = "given"
BIRTH_YEAR = "birth_year"
DEATH_YEAR = "death_year"


def person_comparisons() -> tuple[Comparison, ...]:
    """The untrained comparison columns for a person pair.

    Surname is absent on purpose: it is the blocking key, so every pair the
    model ever sees already agrees on it and the column would carry no
    information. What surname agreement *is* worth for a given pair comes from
    the term-frequency adjustment instead, which is where the signal actually
    lives — see :class:`~.fellegi_sunter.TermFrequencyTable`.
    """
    return (
        Comparison(
            name=GIVEN,
            levels=tuple(level.name for level in GivenLevel),
            # No null level: a bare surname is evidence of *something*, and
            # what it is worth is fitted like any other level (#202).
        ),
        Comparison(
            name=BIRTH_YEAR,
            levels=tuple(level.name for level in YearLevel),
            null_level=YearLevel.ABSENT.name,
        ),
        Comparison(
            name=DEATH_YEAR,
            levels=tuple(level.name for level in YearLevel),
            null_level=YearLevel.ABSENT.name,
        ),
    )


@dataclass(frozen=True)
class PersonProfile:
    """One side of a comparison: the parsed primary name plus whatever
    corroborating facts are known about the person."""

    name: PersonName
    birth_year: int | None = None
    aliases: tuple[PersonName, ...] = ()
    death_year: int | None = None


def profile_readings(profile: PersonProfile) -> Iterator[PersonName]:
    """Every name a profile can be compared under, primary first.

    The primary name comes under all its readings; aliases leave out the
    surname-first one. An alias list is already a set of alternative names,
    often in both orders, and wikidata's run to dozens of scripts and
    nicknames each: reading every one of them backwards too let "T.I." meet
    "ATB" through two Persian aliases on the live corpus.
    """
    yield from profile.name.readings()
    for alias in profile.aliases:
        yield from (reading for reading in alias.readings() if reading.reading != REORDERED)


def pairings(a: PersonProfile, b: PersonProfile) -> Iterator[tuple[PersonName, PersonName]]:
    """Every (name, name) pairing of two profiles that agrees on a surname.

    Each side contributes its names under every reading (:func:`profile_readings`),
    so "Ye Xiaogang" meets "Xiaogang Ye" under the surname ``ye``. Primaries
    come first: callers with no model to rank pairings by take the first one.
    """
    names_b = tuple(profile_readings(b))
    for an in profile_readings(a):
        for bn in names_b:
            if comparable(an, bn):
                yield an, bn


def deciding_names(a: PersonProfile, b: PersonProfile) -> tuple[PersonName, PersonName]:
    """The pairing a model-free caller compares two profiles on: the first one
    that agrees on a surname, else the two primary names as they stand."""
    return next(pairings(a, b), (a.name, b.name))


@dataclass(frozen=True)
class Corpus:
    """Corpus-level term frequencies the model needs at scoring time.

    Kept out of :class:`~.fellegi_sunter.LinkageModel` because they are a
    property of the data being deduped, not of the trained parameters: a
    rebuild changes the frequencies without invalidating the model.
    """

    surnames: TermFrequencyTable
    given_names: TermFrequencyTable

    @classmethod
    def empty(cls) -> Corpus:
        return cls(
            surnames=TermFrequencyTable(0.0, {}),
            given_names=TermFrequencyTable(0.0, {}),
        )


def _shared_given_key(a: PersonName, b: PersonName, level: GivenLevel) -> str | None:
    """The given-name string whose rarity the pair actually shares, if any."""
    if level not in (GivenLevel.EXACT, GivenLevel.PREFIX):
        return None  # nothing spelled out is shared, so rarity says nothing
    shorter = a.given if len(a.given) <= len(b.given) else b.given
    return " ".join(shorter) or None


def comparison_levels(a: PersonName, b: PersonName, pa: PersonProfile, pb: PersonProfile) -> tuple[str, ...]:
    """The level names for one (name, name) comparison of two profiles."""
    return (
        given_level(a, b).name,
        year_level(pa.birth_year, pb.birth_year).name,
        year_level(pa.death_year, pb.death_year).name,
    )


def _method(levels: tuple[str, ...]) -> str:
    """A compact description of what decided the pair, for the review queue."""
    given, birth, death = levels
    parts = [f"given:{given.lower()}"]
    if birth != YearLevel.ABSENT.name:
        parts.append(f"born:{birth.lower()}")
    if death != YearLevel.ABSENT.name:
        parts.append(f"died:{death.lower()}")
    return "+".join(parts)[:50]


def _scored(name: PersonName, profile: PersonProfile) -> bool:
    """Whether a profile is compared under ``name`` (one of its readings).

    A bare-surname alias of a record that has given names is left out. It says
    nothing the primary name does not, and since a bare surname cannot
    conflict, it would otherwise win over the primary name's comparison and
    turn "Leopold Mozart" (alias "Mozart") against "Wolfgang Amadeus Mozart"
    from a given-name conflict into a surname-only pair (#202).
    """
    return bool(name.given) or not profile.name.given


@dataclass(frozen=True)
class PersonScorer:
    """A trained model plus the corpus frequencies it needs to apply."""

    model: LinkageModel
    corpus: Corpus = Corpus.empty()

    def score_names(self, a: PersonName, b: PersonName, pa: PersonProfile, pb: PersonProfile) -> float:
        levels = comparison_levels(a, b, pa, pb)
        return self.model.match_probability(levels, self.tf_bits(a, b, levels))

    def tf_bits(self, a: PersonName, b: PersonName, levels: tuple[str, ...]) -> float:
        bits = self.corpus.surnames.bits(a.surname) if a.surname == b.surname else 0.0
        shared = _shared_given_key(a, b, GivenLevel[levels[0]])
        if shared is not None:
            bits += self.corpus.given_names.bits(shared)
        return bits

    def score(self, a: PersonProfile, b: PersonProfile) -> tuple[float, str]:
        """Posterior P(same person) in [0, 1] with the method that decided it.

        Aliases are tried alongside the primary names, and every name under each
        of its readings, and the strongest pairing wins — a record filed under a
        stage name can still match the legal name of the same person, and "Ye
        Xiaogang" can match "Xiaogang Ye" (#201).

        Only pairings that agree on the surname are considered (see
        :func:`pairings`). Blocking guarantees one such pairing exists, but an
        alias list can contain any surname at all, and comparing given names
        across two unrelated ones is meaningless: it let "Béla Balázs" match the
        alias "B. V. Asaf'ev" on initials, because nothing required `balazs` and
        `asaf'ev` to be the same surname. A pair with no surname in common is
        not comparable.
        """
        best_score = 0.0
        best_levels: tuple[str, ...] = ()
        for an, bn in pairings(a, b):
            if not (_scored(an, a) and _scored(bn, b)):
                continue
            levels = comparison_levels(an, bn, a, b)
            value = self.model.match_probability(levels, self.tf_bits(an, bn, levels))
            if not best_levels or value > best_score:
                best_score, best_levels = value, levels
        return (best_score, _method(best_levels)) if best_levels else (0.0, "surname_gate")


@lru_cache(maxsize=1)
def default_model() -> LinkageModel:
    """The parameters trained by ``composer person-train``, loaded once."""
    return LinkageModel.load(MODEL_PATH)


def score(a: PersonProfile, b: PersonProfile) -> tuple[float, str]:
    """Score a pair with the shipped model and no corpus frequencies.

    Convenience for callers with a single pair in hand. The dedupe pass builds
    a :class:`PersonScorer` with real frequencies instead — without them every
    surname is treated as equally rare, which is precisely the weakness this
    model exists to fix, so treat these scores as a floor.
    """
    return PersonScorer(default_model()).score(a, b)


def _given_level(method: str) -> GivenLevel | None:
    """The given-name level a :func:`_method` string records, if any."""
    head = method.split("+", 1)[0]
    if not head.startswith("given:"):
        return None
    return GivenLevel.__members__.get(head.removeprefix("given:").upper())


def classify(value: float, method: str = "") -> str:
    """The decision for a pair's score, given the method that produced it.

    A pair decided on a bare surname (``given:one_surname_only`` or
    ``given:both_surname_only``) is never auto-linked, however rare the
    surname: every namesake in the block scores the same, so a link would be a
    guess between them (#202). It is queued for review from its own, lower
    cut-point instead — see ``MODEL.md`` for what each one measured.
    """
    if _given_level(method) in SURNAME_ONLY:
        return "needs_review" if value >= SURNAME_ONLY_REVIEW_THRESHOLD else "distinct"
    if value >= AUTO_THRESHOLD:
        return "auto_linked"
    if value >= REVIEW_THRESHOLD:
        return "needs_review"
    return "distinct"
