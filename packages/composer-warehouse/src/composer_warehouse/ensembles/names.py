"""The names an ensemble is matched on.

``dedup_key`` already folds case, accents and punctuation; what it keeps are
the spellings that differ in words: "The Vienna Philharmonic", "Vienna
Philharmonic Orchestra", "Wiener Philharmoniker". The match key drops a leading
article and maps a few ensemble words onto one spelling, so those three meet
once the language half ("Wiener" ↔ "Vienna") is supplied by the anchor's own
aliases. The tables only grow on evidence: every entry merges names, and a
loose one folds "Junge Philharmonie Wien" into the orchestra whose name it
borrows.
"""

from __future__ import annotations

import re

from composer_models.normalize import dedup_key

_ARTICLES = frozenset({"the", "der", "die", "das", "les", "le", "la", "het", "de", "il"})

_WORDS = {
    "philharmoniker": "philharmonic",
    "orchester": "orchestra",
    "orchestre": "orchestra",
    "orkest": "orchestra",
}

# Applied after the word table, so "Philharmoniker" and "Philharmonic
# Orchestra" both come out as the bare "philharmonic".
_PHRASES = (("philharmonic orchestra", "philharmonic"),)

_MEMBERS_OF = re.compile(r"^(?:the )?(?:members of|mitglieder (?:der|des)) (?:the |der |des )?(.+)$")


def ensemble_match_key(name: str) -> str:
    """The key two names of one ensemble share."""
    tokens = dedup_key(name).split()
    if len(tokens) > 1 and tokens[0] in _ARTICLES:
        tokens = tokens[1:]
    key = " ".join(_WORDS.get(token, token) for token in tokens)
    for phrase, replacement in _PHRASES:
        key = re.sub(rf"\b{phrase}\b", replacement, key)
    return key


def members_of(name: str) -> str | None:
    """The ensemble *name* is a subset of ("Members of the Wiener
    Philharmoniker" → "wiener philharmoniker"), or None. A subset is its own
    ensemble and is never folded onto the whole."""
    found = _MEMBERS_OF.match(dedup_key(name))
    return found.group(1) if found else None
