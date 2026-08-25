"""The one entity kind the warehouse does not take on trust: ``person``.

``kind`` is deliberately an open set — a source names the kind of the entity it
reports and the warehouse stores it. That holds for everything a source knows
it is reporting, and breaks for the one thing none of them knows: every source
that credits concert or recording participants mints one entity per credited
*name*, and a credit line is as often an orchestra, a choir or a quartet as it
is a musician. Those all arrive as ``person`` (#174) — the scrapers hard-code
it for a credit, and the LLM extractor emits one ``person`` document per named
participant — which puts "Tölzer Knabenchor" into the person dedupe pass, whose
surname gate reads ``Knabenchor`` as a surname, and into every ``kind='person'``
read downstream.

So the label decides, at the point of writing, whether a purported person is
really an ensemble. The vocabulary below is deliberately narrow: it holds words
that name a *group of performers* and appear in no musician's name, in the
languages the sources actually publish in. A conservative list leaves some
ensembles behind ("Academy of St Martin in the Fields" has no such word) — the
opposite error, re-kinding a real person, silently drops them from the person
reads, so the list only grows on evidence.
"""

from __future__ import annotations

import re
import unicodedata

PERSON_KIND = "person"
ENSEMBLE_KIND = "ensemble"

#: Whole words that name an ensemble. Matched against the label's tokens, so
#: "Escher String Quartet" hits and "Quartetto"-as-a-surname would have to be
#: exactly that to.
_ENSEMBLE_WORDS = frozenset(
    {
        # voices
        "choir",
        "chorale",
        "chorus",
        "chor",
        "choeur",
        "chœur",
        "coro",
        "koor",
        "cantorei",
        "singers",
        "voices",
        # groups by size
        "duo",
        "trio",
        "quartet",
        "quartett",
        "quartetto",
        "quatuor",
        "kwartet",
        "cuarteto",
        "quintet",
        "quintett",
        "quintetto",
        "quintette",
        "sextet",
        "sextett",
        "septet",
        "octet",
        "nonet",
        # groups by kind
        "ensemble",
        "ensembles",
        "camerata",
        "cappella",
        "capella",
        "collegium",
        "consort",
        "kapelle",
        "musici",
    }
)

#: Word beginnings that name an ensemble. A prefix covers a family of
#: inflections in one entry: "philharmon" takes philharmonic, philharmonie,
#: philharmoniker, philharmonique and philharmonisch.
_ENSEMBLE_PREFIXES = (
    "orchestr",
    "orkest",
    "orkiestr",
    "orquest",
    "philharmon",
    "filharmon",
    "symphon",
    "sinfoni",
)

#: Compound heads: German and Dutch write the ensemble word onto the end of the
#: name ("Gewandhausorchester", "Koninklijk Concertgebouworkest").
_ENSEMBLE_HEADS = (
    "orchester",
    "orchestra",
    "orchestre",
    "orkest",
    "ensemble",
    "consort",
    "kapelle",
    "quartett",
    "kwartet",
    "koor",
    "chor",
)

#: How much word has to precede a compound head for it to read as a compound.
#: "Bachchor" is a choir; the surname "Bachor" is not, and only this keeps the
#: two apart.
_MIN_COMPOUND_STEM = 3

_WORD = re.compile(r"[^\W_]+")


def _tokens(label: str) -> list[str]:
    """The label's words, lowercased and stripped of diacritics.

    Deliberately not :func:`composer_models.normalize.dedup_key`: that function
    seeds entity uuids and must not shift underneath them, and this tier cannot
    import it anyway.
    """
    decomposed = unicodedata.normalize("NFKD", label)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return _WORD.findall(folded)


def _is_ensemble_word(token: str) -> bool:
    if token in _ENSEMBLE_WORDS:
        return True
    if token.startswith(_ENSEMBLE_PREFIXES):
        return True
    return any(
        token.endswith(head) and len(token) - len(head) >= _MIN_COMPOUND_STEM for head in _ENSEMBLE_HEADS
    )


def looks_like_ensemble(label: str) -> bool:
    """Whether *label* names a group of performers rather than one musician."""
    return any(_is_ensemble_word(token) for token in _tokens(label))


def resolve_entity_kind(kind: str, label: str) -> str:
    """The kind an entity labelled *label* is stored under, given the *kind* its
    source reported.

    Only ``person`` is second-guessed, and only ever towards ``ensemble``: a
    source that says "ensemble", "work" or "place" is describing something it
    fetched under that heading, while "person" is what a participant credit
    defaults to whether or not anyone looked.
    """
    if kind == PERSON_KIND and looks_like_ensemble(label):
        return ENSEMBLE_KIND
    return kind
