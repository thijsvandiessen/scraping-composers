"""Person resolution: decide when two person names denote the same individual.

The pipeline is four layers, each testable on its own:

``extract``
    parses a name label into a structured ``PersonName`` (surname, given names,
    initials, particles).
``compare``
    reduces a pair of those to a discrete comparison vector — how the given
    names agree, how the years agree — and nothing more.
``fellegi_sunter``
    turns a comparison vector into a posterior probability, using m/u
    parameters fitted by unsupervised EM plus a term-frequency adjustment so a
    shared rare surname outweighs a shared common one.
``dedupe``
    drives all of that over the warehouse and records the decisions.

``training`` fits the model offline, ``evaluation`` builds an automatically
labelled holdout and measures any scorer against it. See ``MODEL.md`` for the
current parameters and their measured operating points.
"""

from __future__ import annotations

from .compare import GivenLevel, YearLevel, given_level, year_level
from .corpus import PersonRecord, build_corpus, candidate_pairs, load_records
from .dedupe import dedupe_persons, reset_person_links
from .extract import PersonName, parse_name
from .fellegi_sunter import LinkageModel, TermFrequencyTable, probability
from .match import (
    AUTO_THRESHOLD,
    MODEL_PATH,
    REVIEW_THRESHOLD,
    Corpus,
    PersonProfile,
    PersonScorer,
    classify,
    default_model,
    score,
)
from .training import train

__all__ = [
    "AUTO_THRESHOLD",
    "MODEL_PATH",
    "REVIEW_THRESHOLD",
    "Corpus",
    "GivenLevel",
    "LinkageModel",
    "PersonName",
    "PersonProfile",
    "PersonRecord",
    "PersonScorer",
    "TermFrequencyTable",
    "YearLevel",
    "build_corpus",
    "candidate_pairs",
    "classify",
    "dedupe_persons",
    "default_model",
    "given_level",
    "load_records",
    "parse_name",
    "probability",
    "reset_person_links",
    "score",
    "train",
    "year_level",
]
