"""A self-contained Fellegi-Sunter record-linkage model.

The old person scorer returned hand-tuned constants (``0.95``, ``0.90``,
``0.70``) that looked like probabilities but were not, so ``AUTO_THRESHOLD =
0.90`` could not be chosen on any principled basis (#173). This replaces them
with the standard probabilistic formulation.

For every comparison column the model holds two conditional distributions over
that column's levels:

* ``m[level]`` — P(the pair lands on this level | the pair is a match)
* ``u[level]`` — P(the pair lands on this level | the pair is *not* a match)

Their ratio is a Bayes factor, and because the columns are assumed
conditionally independent given match status, the log-ratios simply add:

    log2 odds(match) = log2 odds(prior) + SUM log2(m[level] / u[level])

which converts back to a posterior probability. That posterior is what the
thresholds now cut on, so a cut-point is a statement about the corpus rather
than an arbitrary constant. It is not perfectly calibrated — conditional
independence is an approximation, and the measured precision at 0.99 is 0.977,
not 0.99 — so treat a threshold as an operating point to be measured, which is
what ``MODEL.md`` and ``composer-ingest person-eval`` are for.

Two refinements matter for this corpus:

*Null levels.* A comparison where one side has no birth year at all carries no
evidence. Such a level is pinned to a Bayes factor of exactly 1 (zero weight)
and dropped from the m/u normalisation, rather than being allowed to soak up
probability mass and tilt the result.

*Term frequency.* Two people sharing the surname ``Sonnenfeld`` is far stronger
evidence than two people sharing ``Smith``, but the old scorer weighted them
identically -- the single biggest structural gap in the issue. For a matching
pair, P(both records show value v) is p_v (it is one person, whose surname is v
with probability p_v); for a non-matching pair it is p_v * p_v (two independent
draws). The Bayes factor is therefore 1/p_v: rarity *is* the evidence. See
:class:`TermFrequencyTable` for how that is folded in without double-counting.

Parameters come from :func:`fit_supervised`, counting levels over the
automatically labelled pairs :mod:`evaluation` derives from external evidence
(MusicBrainz ids, curated aliases, multi-source dates). The prior is estimated
separately and without labels by :func:`estimate_prior_by_moments`.

EM was implemented and rejected: on this corpus the likelihood has no interior
fixed point, climbing monotonically to "every pair is a match" from any start,
which is the usual outcome when matches are under 2% of pairs and conditional
independence only approximately holds. ``MODEL.md`` records the measurements.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Weights are carried in bits (log base 2) throughout: a weight of +1 doubles
# the odds of a match, -1 halves them, which makes the numbers readable.
_LOG2 = math.log(2.0)

# Guards against a level that never occurred in training producing an infinite
# weight. A pseudo-count of half an observation is the usual Jeffreys prior.
_PSEUDO_COUNT = 0.5


def probability(match_weight: float) -> float:
    """Convert a total match weight in bits to a posterior probability."""
    if match_weight > 64.0:  # 2**-64 underflows to 0 anyway; skip the overflow
        return 1.0
    if match_weight < -64.0:
        return 0.0
    return 1.0 / (1.0 + 2.0**-match_weight)


def log_odds(prob: float) -> float:
    """Convert a probability to log2 odds."""
    prob = min(max(prob, 1e-12), 1.0 - 1e-12)
    return math.log(prob / (1.0 - prob)) / _LOG2


@dataclass(frozen=True)
class TermFrequencyTable:
    """Per-value frequencies for one comparison column.

    The column's own ``m``/``u`` already price in agreement *on average*, so
    handing the model a raw ``log2(1 / p_v)`` on top would count the average
    twice. The table therefore contributes only the deviation from a reference
    frequency, ``log2(reference / p_v)``: a value exactly as common as the
    reference adds nothing, a rarer one adds evidence, a commoner one subtracts
    it. ``reference`` is the pair-weighted mean frequency, so the adjustment is
    close to zero-sum across the corpus and the prior stays interpretable.

    ``max_bits`` clamps the adjustment. A surname seen twice in 200k records
    would otherwise swamp every other column on its own.
    """

    reference: float
    frequencies: Mapping[str, float]
    max_bits: float = 6.0

    def bits(self, value: str) -> float:
        """The term-frequency adjustment for ``value``, in bits."""
        freq = self.frequencies.get(value)
        if not freq or not self.reference:
            return 0.0  # unseen value: no basis for an adjustment
        return max(-self.max_bits, min(self.max_bits, math.log(self.reference / freq) / _LOG2))

    @classmethod
    def from_counts(
        cls, counts: Mapping[str, int], max_bits: float = 6.0, max_count: int | None = None
    ) -> TermFrequencyTable:
        """Build a table from raw value counts.

        ``reference`` is the *geometric* mean of the value frequencies, weighted
        by how often each value is actually compared — a value shared by ``n``
        records generates ``n * (n - 1) / 2`` pairs. Geometric, not arithmetic,
        because the adjustment is a log ratio: only the geometric mean makes
        ``log2(reference / p_v)`` average to zero across the pairs being scored,
        which keeps the adjustment a pure redistribution and leaves the model's
        prior meaning what it says.

        ``max_count`` excludes over-large values from that weighting, and must
        match the caller's blocking cap. Averaging over pairs that blocking
        then refuses to score de-centres the whole table: one 2,642-member
        block of mis-parsed labels held 62% of this corpus's nominal pair mass,
        and dropping it from scoring but not from the reference handed every
        surviving pair a spurious +2 bits. Such values keep their frequencies —
        a pair that reaches them by another route still scores correctly.
        """
        total = sum(counts.values())
        if not total:
            return cls(reference=0.0, frequencies={})
        frequencies = {value: count / total for value, count in counts.items()}
        cap = max_count if max_count is not None else total
        weights = {v: c * (c - 1) / 2 for v, c in counts.items() if 1 < c <= cap}
        weight_total = sum(weights.values())
        if not weight_total:
            return cls(reference=sum(frequencies.values()) / len(frequencies), frequencies=frequencies)
        log_mean = sum(math.log(frequencies[v]) * w for v, w in weights.items()) / weight_total
        return cls(reference=math.exp(log_mean), frequencies=frequencies, max_bits=max_bits)


@dataclass
class Comparison:
    """One comparison column: its levels and their m/u probabilities.

    ``null_level`` names the level meaning "no evidence available" (one side
    lacks the field). It is pinned to zero weight and excluded from the m/u
    distributions, which are normalised over the remaining levels.
    """

    name: str
    levels: tuple[str, ...]
    m: dict[str, float] = field(default_factory=dict[str, float])
    u: dict[str, float] = field(default_factory=dict[str, float])
    null_level: str | None = None

    def informative_levels(self) -> tuple[str, ...]:
        return tuple(level for level in self.levels if level != self.null_level)

    def bits(self, level: str) -> float:
        """The match weight contributed by landing on ``level``, in bits."""
        if level == self.null_level:
            return 0.0
        m = self.m.get(level, _PSEUDO_COUNT)
        u = self.u.get(level, _PSEUDO_COUNT)
        if m <= 0.0 or u <= 0.0:
            return 0.0
        return math.log(m / u) / _LOG2


@dataclass
class LinkageModel:
    """A trained Fellegi-Sunter model: a prior plus one entry per column.

    ``prior`` is P(match) among the pairs the model is *shown* — i.e. pairs
    that already survived blocking — not among all possible pairs.
    """

    comparisons: tuple[Comparison, ...]
    prior: float = 0.001
    trained_on_pairs: int = 0

    def by_name(self, name: str) -> Comparison:
        for comparison in self.comparisons:
            if comparison.name == name:
                return comparison
        raise KeyError(name)

    def match_weight(self, levels: Sequence[str], tf_bits: float = 0.0) -> float:
        """Total match weight in bits for one pair's comparison vector."""
        total = log_odds(self.prior) + tf_bits
        for comparison, level in zip(self.comparisons, levels, strict=True):
            total += comparison.bits(level)
        return total

    def match_probability(self, levels: Sequence[str], tf_bits: float = 0.0) -> float:
        """Posterior P(match) for one pair's comparison vector."""
        return probability(self.match_weight(levels, tf_bits))

    def to_dict(self) -> dict[str, object]:
        return {
            "prior": self.prior,
            "trained_on_pairs": self.trained_on_pairs,
            "comparisons": [
                {
                    "name": c.name,
                    "levels": list(c.levels),
                    "null_level": c.null_level,
                    "m": c.m,
                    "u": c.u,
                }
                for c in self.comparisons
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> LinkageModel:
        raw_comparisons = data["comparisons"]
        assert isinstance(raw_comparisons, list)
        comparisons: list[Comparison] = []
        for raw in raw_comparisons:
            assert isinstance(raw, dict)
            comparisons.append(
                Comparison(
                    name=str(raw["name"]),
                    levels=tuple(str(level) for level in raw["levels"]),
                    null_level=None if raw["null_level"] is None else str(raw["null_level"]),
                    m={str(k): float(v) for k, v in dict(raw["m"]).items()},
                    u={str(k): float(v) for k, v in dict(raw["u"]).items()},
                )
            )
        return cls(
            comparisons=tuple(comparisons),
            prior=float(data["prior"]),  # type: ignore[arg-type]
            trained_on_pairs=int(data.get("trained_on_pairs", 0)),  # type: ignore[arg-type]
        )

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> LinkageModel:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Pattern:
    """A distinct comparison vector, with its term-frequency bucket.

    EM only ever needs *how many* pairs landed on each vector, and the vectors
    are discrete, so 1.4M blocked pairs collapse to a few thousand patterns and
    the whole estimation runs in memory in pure Python. ``tf_bits`` is rounded
    into buckets to keep that collapse effective.
    """

    levels: tuple[str, ...]
    tf_bits: float


TF_BUCKET = 0.25
"""Width, in bits, of the term-frequency buckets patterns are rounded into."""


def bucket_tf(bits: float) -> float:
    return round(bits / TF_BUCKET) * TF_BUCKET


def _normalise(weights: Mapping[str, float], levels: Sequence[str]) -> dict[str, float]:
    """Smooth and normalise accumulated level weights into a distribution."""
    smoothed = {level: weights.get(level, 0.0) + _PSEUDO_COUNT for level in levels}
    total = sum(smoothed.values())
    return {level: value / total for level, value in smoothed.items()}


def observed_distribution(
    patterns: Mapping[Pattern, int], comparisons: Sequence[Comparison]
) -> list[dict[str, float]]:
    """The level distribution actually seen across ``patterns``, per column."""
    accumulators: list[Counter[str]] = [Counter() for _ in comparisons]
    for pattern, count in patterns.items():
        for index, level in enumerate(pattern.levels):
            accumulators[index][level] += count
    return [
        _normalise(accumulators[index], comparison.informative_levels())
        for index, comparison in enumerate(comparisons)
    ]


def estimate_prior_by_moments(
    observed: Mapping[str, float], u: Mapping[str, float], levels: Sequence[str]
) -> float:
    """P(match) among blocked pairs, from one column's marginals. No labels.

    A blocked pair is a match with probability ``L``, so the level distribution
    we observe is the mixture ``observed = L * m + (1 - L) * u``. With ``u``
    known from random sampling, solving for ``m`` gives
    ``m = (observed - (1 - L) * u) / L``, and ``m`` is a probability, so it
    cannot go negative. That single constraint pins ``L`` from below:

        L >= 1 - min over levels of (observed / u)

    The bound binds on the disagreement level, where blocked pairs agree
    *less* often than chance because a slice of them are the same person. Taken
    as an equality it is the smallest prior consistent with the data — the
    conservative reading, and the one used here.

    This is the estimate the model ships with because it needs neither labels
    nor EM: EM was tried on this corpus and has no interior fixed point (the
    likelihood climbs monotonically to "every pair is a match"), which is the
    usual outcome when conditional independence is only approximately true and
    matches are under 2% of pairs.
    """
    ratios = [observed[level] / u[level] for level in levels if u.get(level)]
    if not ratios:
        return 0.01
    return min(max(1.0 - min(ratios), 1e-6), 0.5)


@dataclass(frozen=True)
class LabelledVector:
    """One comparison vector with a known outcome, for supervised fitting."""

    levels: tuple[str, ...]
    is_match: bool
    weight: float = 1.0
    columns: tuple[str, ...] | None = None
    """Restrict this row to informing only these columns; ``None`` means all.

    A label derived from birth years says nothing trustworthy about the birth
    year column — the rule that produced it *is* that feature — so such a row
    is admitted for the name columns only.
    """


def fit_supervised(
    vectors: Sequence[LabelledVector],
    comparisons: Sequence[Comparison],
    prior: float,
) -> LinkageModel:
    """Estimate m and u from labelled comparison vectors.

    Counting how often each level occurs among known matches gives ``m``
    directly, and among known non-matches gives ``u`` — the definitions of
    both, with no mixture to disentangle. ``prior`` is supplied separately
    (see :func:`estimate_prior_by_moments`) because the labelled pairs are a
    deliberately unrepresentative slice and their match rate is not the
    corpus's.
    """
    model = LinkageModel(comparisons=tuple(comparisons), prior=prior)
    m_acc: list[dict[str, float]] = [{} for _ in model.comparisons]
    u_acc: list[dict[str, float]] = [{} for _ in model.comparisons]

    for vector in vectors:
        for index, comparison in enumerate(model.comparisons):
            if vector.columns is not None and comparison.name not in vector.columns:
                continue
            target = m_acc if vector.is_match else u_acc
            level = vector.levels[index]
            target[index][level] = target[index].get(level, 0.0) + vector.weight

    for index, comparison in enumerate(model.comparisons):
        informative = comparison.informative_levels()
        comparison.m = _normalise(m_acc[index], informative)
        comparison.u = _normalise(u_acc[index], informative)

    model.trained_on_pairs = len(vectors)
    return model
