"""Applying the controlled vocabulary to what the model coined.

The terms themselves live in :mod:`.vocabulary` and are re-exported here, so the
rest of the package keeps importing predicates from one place; this module is the
behaviour — slugifying a coined predicate, folding it onto a vocabulary term,
and answering what shape its object takes.
"""

from __future__ import annotations

import re

from .vocabulary import ALIASES, DENYLIST, LITERAL_FORMS, OBJECT_KINDS, VOCABULARY

__all__ = [
    "ALIASES",
    "DENYLIST",
    "LITERAL_FORMS",
    "OBJECT_KINDS",
    "VOCABULARY",
    "is_known",
    "literal_form",
    "normalize_predicate",
    "object_kind_for",
    "slugify",
    "takes_literal",
    "vocabulary_hint",
]

_NON_WORD = re.compile(r"[^\w]+")
_UNDERSCORES = re.compile(r"_{2,}")


def slugify(raw: str) -> str:
    """``"First LA Phil Performance"`` -> ``"first_la_phil_performance"``."""
    slug = _NON_WORD.sub("_", raw.strip().lower())
    return _UNDERSCORES.sub("_", slug).strip("_")


def normalize_predicate(raw: str) -> str | None:
    """The vocabulary term *raw* denotes, or ``None`` if it may not be stored.

    Returns a slugified predicate for anything outside the vocabulary rather than
    rejecting it — that is what keeps extraction open — so callers should treat a
    result outside :data:`~.vocabulary.VOCABULARY` as worth counting.
    """
    slug = slugify(raw)
    if not slug:
        return None
    slug = ALIASES.get(slug, slug)
    if slug in DENYLIST:
        return None
    return slug


def object_kind_for(predicate: str) -> str | None:
    """The entity kind *predicate*'s object is, or ``None`` if it takes a literal
    or is a predicate nobody has declared."""
    return OBJECT_KINDS.get(predicate)


def takes_literal(predicate: str) -> bool:
    """Whether *predicate* is a vocabulary term whose object is a literal."""
    return predicate in VOCABULARY and predicate not in OBJECT_KINDS


def literal_form(predicate: str) -> str:
    """The predicate *predicate* denotes when its object is a literal."""
    return LITERAL_FORMS.get(predicate, predicate)


def is_known(predicate: str) -> bool:
    """Whether *predicate* is part of the curated vocabulary."""
    return predicate in VOCABULARY


def vocabulary_hint() -> str:
    """The vocabulary as the prompt lists it, sorted so the prompt text — and so
    every cache key derived from it — stays stable across runs."""
    return ", ".join(sorted(VOCABULARY))
