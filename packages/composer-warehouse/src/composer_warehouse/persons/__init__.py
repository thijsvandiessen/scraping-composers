"""Person resolution: detect when two person names denote the same individual.

``extract`` parses a name label into a structured ``PersonName`` (surname,
given names, initials, particles); ``match`` scores two of those (optionally
with birth years) and decides match / review / distinct. The ``dedupe`` pass
drives this over existing person entities. This mirrors ``works/`` but is kept
deliberately small — a few heuristics now, more clauses later.
"""

from __future__ import annotations

from .dedupe import dedupe_persons
from .extract import PersonName, parse_name
from .match import AUTO_THRESHOLD, REVIEW_THRESHOLD, PersonProfile, classify, score

__all__ = [
    "AUTO_THRESHOLD",
    "REVIEW_THRESHOLD",
    "PersonName",
    "PersonProfile",
    "classify",
    "dedupe_persons",
    "parse_name",
    "score",
]
