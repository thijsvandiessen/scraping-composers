"""Fit the person linkage model to a warehouse.

Run offline (``composer person-train``); the fitted parameters are written to
``model.json`` and committed, so the dedupe pass never trains at runtime.

Nothing here is hand-labelled. The two ingredients are:

``u`` and ``m``
    counted over the automatically labelled pairs from :mod:`evaluation`,
    whose labels come from MusicBrainz ids, curated wikidata aliases and
    multi-source date agreement — external evidence, not our own matcher.
    Only the training split is used; the test split never touches a parameter.
``prior``
    :func:`estimate_prior_by_moments`, from the level distribution of random
    pairs versus blocked pairs. No labels at all.

Labels derived from dates are admitted for the *name* column only. Letting a
date-derived label set the weight of the date column would be measuring a rule
against itself; see :data:`YEAR_DERIVED`.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .corpus import PersonRecord, build_corpus, candidate_pairs, load_records
from .evaluation import MATCH, LabelledPair, build_labels, profiles, split
from .fellegi_sunter import (
    LabelledVector,
    LinkageModel,
    Pattern,
    bucket_tf,
    estimate_prior_by_moments,
    fit_supervised,
    observed_distribution,
)
from .match import GIVEN, Corpus, PersonScorer, comparison_levels, person_comparisons

log = logging.getLogger(__name__)

U_SAMPLE_PAIRS = 3_000_000
"""Random pairs drawn to estimate the non-match level distribution."""

YEAR_DERIVED = frozenset({"dates_corroborated", "year_conflict"})
"""Label provenances that used the year fields, and so may only inform names."""

NAME_ONLY: tuple[str, ...] = (GIVEN,)


def _scorer(corpus: Corpus) -> PersonScorer:
    """A weightless scorer, used only for its term-frequency arithmetic."""
    return PersonScorer(LinkageModel(comparisons=person_comparisons()), corpus)


def pair_tf_bits(corpus: Corpus, a: PersonRecord, b: PersonRecord) -> float:
    """The term-frequency adjustment for a record pair."""
    scorer = _scorer(corpus)
    pa, pb = a.profile(), b.profile()
    return scorer.tf_bits(pa.name, pb.name, comparison_levels(pa.name, pb.name, pa, pb))


def collect_patterns(records: Sequence[PersonRecord], corpus: Corpus) -> dict[Pattern, int]:
    """Comparison-vector counts over every candidate pair.

    Two million pairs collapse to a few thousand distinct patterns, which is
    what keeps the corpus-wide statistics cheap in pure Python.
    """
    scorer = _scorer(corpus)
    counts: dict[Pattern, int] = {}
    for a, b in candidate_pairs(records):
        pa, pb = a.profile(), b.profile()
        levels = comparison_levels(pa.name, pb.name, pa, pb)
        pattern = Pattern(levels=levels, tf_bits=bucket_tf(scorer.tf_bits(pa.name, pb.name, levels)))
        counts[pattern] = counts.get(pattern, 0) + 1
    return counts


def sample_non_match_levels(
    records: Sequence[PersonRecord],
    pairs: int = U_SAMPLE_PAIRS,
    seed: int = 173,
) -> list[dict[str, float]]:
    """Level distribution over random pairs, per column.

    Two people drawn at random from 212k are not the same person, so this is
    P(level | non-match) measured directly. It differs sharply from the same
    distribution over *blocked* pairs — random pairs share a full given name
    0.08% of the time, blocked pairs 1.6% — and that gap is the entire match
    signal the prior is recovered from.
    """
    comparisons = person_comparisons()
    counts: list[dict[str, float]] = [dict.fromkeys(c.informative_levels(), 0.0) for c in comparisons]
    rng = random.Random(seed)
    size = len(records)
    for _ in range(pairs):
        a, b = records[rng.randrange(size)], records[rng.randrange(size)]
        if a.entity_id == b.entity_id:
            continue
        pa, pb = a.profile(), b.profile()
        for index, level in enumerate(comparison_levels(pa.name, pb.name, pa, pb)):
            if level in counts[index]:
                counts[index][level] += 1.0
    return [
        {level: value / total for level, value in column.items()}
        if (total := sum(column.values()))
        else column
        for column in counts
    ]


def estimate_prior(records: Sequence[PersonRecord], corpus: Corpus) -> float:
    """P(match) among blocked pairs, without using any label."""
    comparisons = person_comparisons()
    observed = observed_distribution(collect_patterns(records, corpus), comparisons)
    sampled = sample_non_match_levels(records)
    given = comparisons[0]
    return estimate_prior_by_moments(observed[0], sampled[0], given.informative_levels())


def to_vectors(pairs: Sequence[LabelledPair]) -> list[LabelledVector]:
    """Turn labelled rows into the comparison vectors ``fit_supervised`` counts."""
    vectors: list[LabelledVector] = []
    for pair in pairs:
        a, b = profiles(pair)
        vectors.append(
            LabelledVector(
                levels=comparison_levels(a.name, b.name, a, b),
                is_match=pair.label == MATCH,
                weight=pair.weight,
                columns=NAME_ONLY if pair.provenance in YEAR_DERIVED else None,
            )
        )
    return vectors


@dataclass(frozen=True)
class TrainingResult:
    model: LinkageModel
    corpus: Corpus
    train: list[LabelledPair]
    test: list[LabelledPair]
    labelled: list[LabelledPair]


def train(session: Session) -> TrainingResult:
    """Load the warehouse, label it automatically, and fit the model."""
    records = load_records(session)
    corpus = build_corpus(records)
    log.info("loaded %d person records", len(records))

    prior = estimate_prior(records, corpus)
    log.info("prior P(match | blocked pair) = %.5f", prior)

    labelled = build_labels(records, lambda a, b: pair_tf_bits(corpus, a, b))
    train_rows, test_rows = split(labelled)
    log.info("labelled %d pairs (%d train / %d test)", len(labelled), len(train_rows), len(test_rows))

    model = fit_supervised(to_vectors(train_rows), person_comparisons(), prior)
    return TrainingResult(model=model, corpus=corpus, train=train_rows, test=test_rows, labelled=labelled)
