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
``cluster``
    turns the recorded pairs into disjoint duplicate groups, so a link is one
    hop to a cluster canonical and a constraint has a group to apply to.
``constraints``
    derives the cannot-links from authority ids — two Wikidata QIDs are two
    people unless corroboration says otherwise.

``training`` fits the model offline, ``evaluation`` builds an automatically
labelled holdout and measures any scorer against it. See ``MODEL.md`` for the
current parameters and their measured operating points.
"""

from __future__ import annotations

from .cluster import Clustering, Edge, build_clusters
from .compare import GivenLevel, YearLevel, given_level, year_level
from .constraints import Conflict, Constraints, authority_constraints
from .corpus import PersonRecord, alias_identity, build_corpus, candidate_pairs, load_records
from .dedupe import DedupeResult, Partition, apply_clusters, dedupe_persons, reset_person_links
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
    "Clustering",
    "Conflict",
    "Constraints",
    "Corpus",
    "DedupeResult",
    "Edge",
    "GivenLevel",
    "LinkageModel",
    "Partition",
    "PersonName",
    "PersonProfile",
    "PersonRecord",
    "PersonScorer",
    "TermFrequencyTable",
    "YearLevel",
    "alias_identity",
    "apply_clusters",
    "authority_constraints",
    "build_clusters",
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
