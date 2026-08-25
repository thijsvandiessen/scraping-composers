"""Build a labelled evaluation set automatically, and measure a scorer on it.

The issue's third problem was that there was no way to tell whether any change
to the matcher was an improvement. Fixing that needs ground truth, and hand-
judging a few hundred pairs does not scale to a corpus of 212k people. So the
labels are derived from evidence the *name* scorer never sees:

Matches
    ``dates_corroborated`` — birth and death year both agree and the agreement
    is backed by at least two distinct sources. One record saying 1685 is a
    claim; three sources saying 1685 for both sides of a pair is evidence.
    ``alias_identity`` — one record's name appears verbatim in the other's
    ``also_known_as`` list, and the two surface names differ. Wikidata's own
    alias curation, not our matcher's opinion.

Non-matches
    ``year_conflict`` — lifetimes more than a decade apart. Same surname,
    different generation: a father, not a son.
    ``distinct_musicbrainz`` — both records carry a MusicBrainz id and the ids
    differ. MusicBrainz has already decided these are two people.

A pair that trips both a match and a non-match rule is discarded rather than
guessed at; a contested label is worse than no label.

Two honest caveats, both reported alongside the numbers:

* **The year rules overlap with a model feature.** A model that uses birth year
  gets ``dates_corroborated`` and ``year_conflict`` pairs partly for free. That
  is why :func:`evaluate` reports every stratum separately and why
  ``person-eval`` also runs a names-only ablation — with the year columns
  denied to the model, the year-derived labels become fully independent
  evidence about the part of the scorer that was actually broken.
* **These are labels for the pairs that *can* be labelled.** Sampling weights
  restore each stratum's true share of that population, so the numbers estimate
  precision over labellable pairs — not over every pair in the corpus.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .corpus import PersonRecord, alias_identity, candidate_pairs
from .extract import parse_name
from .match import PersonProfile, PersonScorer, comparison_levels

MATCH = "match"
NON_MATCH = "non_match"

# A pair whose lifetimes are this far apart is a different generation.
GENERATION_GAP = 10
# Years agreeing within this are the ordinary disagreement between sources.
YEAR_TOLERANCE = 1
# How many distinct sources must back the dates before they count as evidence.
MIN_CORROBORATING_SOURCES = 2


@dataclass(frozen=True)
class LabelledPair:
    """One evaluation row, self-contained so the frozen dataset needs no DB."""

    a_label: str
    b_label: str
    a_birth: int | None
    b_birth: int | None
    a_death: int | None
    b_death: int | None
    label: str
    provenance: str
    tf_bits: float
    weight: float = 1.0


def _year_conflict(a: PersonRecord, b: PersonRecord) -> bool:
    for left, right in ((a.birth_years, b.birth_years), (a.death_years, b.death_years)):
        if left and right and min(abs(x - y) for x in left for y in right) > GENERATION_GAP:
            return True
    return False


def _dates_corroborated(a: PersonRecord, b: PersonRecord) -> bool:
    """Birth *and* death agree, backed by at least two distinct sources."""
    if a.birth_year is None or b.birth_year is None or a.death_year is None or b.death_year is None:
        return False
    if abs(a.birth_year - b.birth_year) > YEAR_TOLERANCE:
        return False
    if abs(a.death_year - b.death_year) > YEAR_TOLERANCE:
        return False
    corroborating = (a.birth_sources | b.birth_sources) & (a.death_sources | b.death_sources)
    return len(corroborating) >= MIN_CORROBORATING_SOURCES


def label_pair(a: PersonRecord, b: PersonRecord) -> tuple[str, str] | None:
    """``(label, provenance)`` for a pair, or ``None`` if it can't be judged."""
    # Ordered by preference, not by strength: a pair that trips both rules is
    # filed under the MusicBrainz one because that label owes nothing to the
    # year fields, which lets it inform the year columns during fitting.
    negatives: list[str] = []
    if a.musicbrainz_ids and b.musicbrainz_ids and not (a.musicbrainz_ids & b.musicbrainz_ids):
        negatives.append("distinct_musicbrainz")
    if _year_conflict(a, b):
        negatives.append("year_conflict")

    positives: list[str] = []
    if _dates_corroborated(a, b):
        positives.append("dates_corroborated")
    if alias_identity(a, b):
        positives.append("alias_identity")

    if negatives and positives:
        return None  # contested: the sources disagree about identity itself
    if negatives:
        return NON_MATCH, negatives[0]
    if positives:
        return MATCH, positives[0]
    return None


def build_labels(
    records: Sequence[PersonRecord],
    tf_bits: Callable[[PersonRecord, PersonRecord], float],
) -> list[LabelledPair]:
    """Label every candidate pair that any rule can decide."""
    labelled: list[LabelledPair] = []
    for a, b in candidate_pairs(records):
        decided = label_pair(a, b)
        if decided is None:
            continue
        label, provenance = decided
        labelled.append(
            LabelledPair(
                a_label=a.label,
                b_label=b.label,
                a_birth=a.birth_year,
                b_birth=b.birth_year,
                a_death=a.death_year,
                b_death=b.death_year,
                label=label,
                provenance=provenance,
                tf_bits=round(tf_bits(a, b), 4),
            )
        )
    return labelled


def downsample(
    labelled: Sequence[LabelledPair],
    caps: Mapping[str, int],
    seed: int = 173,
    protect: Callable[[LabelledPair], bool] | None = None,
) -> list[LabelledPair]:
    """Cap each provenance stratum, recording the weight needed to undo it.

    Keeping all 520k ``year_conflict`` negatives would bloat the committed
    dataset and drown the strata that actually discriminate. Sampling is fine
    as long as precision is computed on the *reweighted* counts, which is what
    ``weight`` is for.

    Uniform sampling alone is not fine, though. False positives are rare and
    concentrated in the handful of negatives that score near the threshold, so
    a 1-in-18 sample can leave a single such row standing in for eighteen and
    makes the precision estimate lurch. ``protect`` marks those contested rows;
    they are all kept at weight 1 and excluded from the cap, so the false
    positives are counted exactly and only the inert negatives — which cannot
    become false positives at any usable threshold — are sampled.
    """
    protected: list[LabelledPair] = []
    by_provenance: dict[str, list[LabelledPair]] = {}
    for pair in labelled:
        if protect is not None and protect(pair):
            protected.append(pair)
        else:
            by_provenance.setdefault(pair.provenance, []).append(pair)

    rng = random.Random(seed)
    kept: list[LabelledPair] = list(protected)
    for provenance, rows in sorted(by_provenance.items()):
        cap = caps.get(provenance, len(rows))
        if len(rows) <= cap:
            kept.extend(rows)
            continue
        weight = len(rows) / cap
        kept.extend(replace(pair, weight=round(weight, 6)) for pair in rng.sample(rows, cap))
    return kept


def split(
    pairs: Sequence[LabelledPair], *, test_fraction: float = 0.5, seed: int = 173
) -> tuple[list[LabelledPair], list[LabelledPair]]:
    """Deterministically split into (train, test).

    The split is a hash of the two names, not a shuffle, so a given pair always
    lands on the same side however the dataset is ordered or regenerated — a
    pair cannot drift from test to train between runs and quietly leak.
    """
    train: list[LabelledPair] = []
    test: list[LabelledPair] = []
    cut = int(test_fraction * 2**32)
    for pair in pairs:
        key = f"{seed}:{min(pair.a_label, pair.b_label)}|{max(pair.a_label, pair.b_label)}"
        bucket = int(hashlib.blake2b(key.encode("utf-8"), digest_size=4).hexdigest(), 16)
        (test if bucket < cut else train).append(pair)
    return train, test


def write_dataset(path: Path, pairs: Iterable[LabelledPair]) -> int:
    """Write the evaluation set as gzipped JSONL. Returns the row count."""
    written = 0
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for pair in sorted(pairs, key=lambda p: (p.provenance, p.a_label, p.b_label)):
            handle.write(json.dumps(asdict(pair), sort_keys=True) + "\n")
            written += 1
    return written


def read_dataset(path: Path) -> list[LabelledPair]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [LabelledPair(**json.loads(line)) for line in handle if line.strip()]


@dataclass(frozen=True)
class Metrics:
    """Weighted confusion counts and the rates derived from them."""

    true_positive: float = 0.0
    false_positive: float = 0.0
    false_negative: float = 0.0
    true_negative: float = 0.0

    @property
    def precision(self) -> float:
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positive + self.false_negative
        return self.true_positive / actual if actual else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def add(self, label: str, predicted_match: bool, weight: float) -> Metrics:
        tp, fp, fn, tn = self.true_positive, self.false_positive, self.false_negative, self.true_negative
        if label == MATCH and predicted_match:
            tp += weight
        elif label == MATCH:
            fn += weight
        elif predicted_match:
            fp += weight
        else:
            tn += weight
        return Metrics(tp, fp, fn, tn)


ScoreFn = Callable[[LabelledPair], float]


def evaluate(pairs: Sequence[LabelledPair], score_fn: ScoreFn, threshold: float) -> dict[str, Metrics]:
    """Confusion counts overall and per label provenance.

    The per-provenance split is not decoration: ``distinct_musicbrainz`` is the
    stratum the old scorer failed on, and an overall number that averages it
    with the easy strata hides exactly the defect this exists to catch.
    """
    results: dict[str, Metrics] = {"overall": Metrics()}
    for pair in pairs:
        predicted = score_fn(pair) >= threshold
        results["overall"] = results["overall"].add(pair.label, predicted, pair.weight)
        current = results.get(pair.provenance, Metrics())
        results[pair.provenance] = current.add(pair.label, predicted, pair.weight)
    return results


def profiles(pair: LabelledPair, *, with_years: bool = True) -> tuple[PersonProfile, PersonProfile]:
    """Parse a row back into two profiles.

    ``with_years=False`` is the ablation: it hides the dates from the scorer so
    that the date-derived labels become independent evidence about the name
    comparison on its own.
    """
    return (
        PersonProfile(
            name=parse_name(pair.a_label),
            birth_year=pair.a_birth if with_years else None,
            death_year=pair.a_death if with_years else None,
        ),
        PersonProfile(
            name=parse_name(pair.b_label),
            birth_year=pair.b_birth if with_years else None,
            death_year=pair.b_death if with_years else None,
        ),
    )


def model_scorer(scorer: PersonScorer, *, with_years: bool = True) -> ScoreFn:
    """Score rows with a trained :class:`~.match.PersonScorer`.

    The row's stored ``tf_bits`` is reused rather than recomputed: it is a
    snapshot of the corpus the labels were drawn from, which keeps the frozen
    dataset self-contained. It survives the ablation — surname rarity is a
    property of the names, and hiding it would measure something the scorer is
    never actually asked to do.
    """

    def run(pair: LabelledPair) -> float:
        a, b = profiles(pair, with_years=with_years)
        levels = comparison_levels(a.name, b.name, a, b)
        return scorer.model.match_probability(levels, pair.tf_bits)

    return run


def legacy_score(pair: LabelledPair, *, with_years: bool = True) -> float:
    """The pre-#173 hand-tuned scorer, preserved verbatim as the baseline.

    Kept here rather than in :mod:`match` because its only remaining purpose is
    to be beaten: the acceptance criteria ask for the new engine's numbers
    *and* the old scorer's on the same data. The bug is intact on purpose —
    ``_initials_compatible`` compared initials instead of the spelled-out given
    names, so "Jules" and "Jochen" both reduced to ("j",) and scored 0.90.
    """
    a, b = parse_name(pair.a_label), parse_name(pair.b_label)
    if a.surname != b.surname:
        return 0.0

    if a.given and b.given and a.given == b.given:
        base = 0.95
    elif _legacy_initials_compatible(a.given_initials, b.given_initials):
        base = 0.90
    elif not a.given or not b.given:
        base = 0.70
    else:
        base = 0.20

    a_year = pair.a_birth if with_years else None
    b_year = pair.b_birth if with_years else None
    if a_year is not None and b_year is not None:
        if abs(a_year - b_year) > 1:
            return 0.05
        return round(min(1.0, base + 0.2), 4)
    return base


def _legacy_initials_compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return long[: len(short)] == short
