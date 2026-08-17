"""Turning stated scoring into instrumentation entities you can query.

A page writes its scoring for people to read — "Klavier zu vier Händen", "for
string orchestra", "Violine und Klavier" — and that text is kept verbatim as an
``orchestration`` literal. A literal answers no question, though: "every work for
piano" over free prose is a ``LIKE`` across a dozen spellings in two languages.

So the stated text is also folded onto a canonical *scoring category* here, and
:mod:`.claims` emits one ``written_for`` claim per category, pointing at an
``instrumentation`` entity. The category — not the individual instrument — is the
unit, because that is what the catalogues themselves are organised by: Bärenreiter
offers "works for string orchestra" as a facet, and a string orchestra is not a
list of instruments any more than a piano is.

A recognised category that demonstrably contains an instrument also emits that
instrument (:data:`CONTAINS`), so a violin sonata still answers "works for piano".

Nothing here guesses, on the same principle as :mod:`.values`: a phrase the tables
do not recognise yields no category at all rather than an invented one. The
``orchestration`` literal still carries it, and the run log counts it (see
:meth:`~.resilience.ExtractStats.unrecognised_summary`) so the tables can grow.
"""

from __future__ import annotations

import re

#: Canonical scoring category -> the spellings that denote it. Inverted into
#: :data:`_SYNONYMS` below; written this way round because that is how it is read
#: and maintained. German spellings are carried because both catalogues this was
#: grown against serve the same pages in two languages.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "piano": ("klavier", "pianoforte", "pf"),
    "fortepiano": ("hammerklavier",),
    "violin": ("violine", "geige"),
    "viola": ("bratsche",),
    "cello": ("violoncello", "violoncell"),
    "double bass": ("contrabass", "kontrabass", "doublebass", "double-bass"),
    "flute": ("flote", "floete", "querflote"),
    "oboe": (),
    "clarinet": ("klarinette",),
    "bassoon": ("fagott",),
    "horn": ("french horn", "waldhorn"),
    "trumpet": ("trompete",),
    "trombone": ("posaune",),
    "tuba": (),
    "harp": ("harfe",),
    "guitar": ("gitarre",),
    "organ": ("orgel",),
    "harpsichord": ("cembalo",),
    "recorder": ("blockflote",),
    "percussion": ("schlagzeug", "schlagwerk"),
    "voice": ("singstimme", "gesang", "vocal", "voices"),
    "soprano": ("sopran",),
    "alto": ("alt",),
    "tenor": (),
    "bass": (),
    "orchestra": ("orchester", "symphony orchestra", "sinfonieorchester", "grosses orchester"),
    # Not "strings"/"Streicher": in an orchestral scoring list those name the
    # string *section* of a full orchestra, not a work for string orchestra.
    "string orchestra": ("streichorchester",),
    "chamber orchestra": ("kammerorchester",),
    "wind ensemble": ("wind band", "blasorchester", "blaserensemble"),
    "string quartet": ("streichquartett",),
    "string trio": ("streichtrio",),
    "piano trio": ("klaviertrio",),
    "piano quartet": ("klavierquartett",),
    "piano quintet": ("klavierquintett",),
    "wind quintet": ("blaserquintett",),
    "piano four hands": (
        "piano 4 hands",
        "four hands",
        "piano duet",
        "klavier zu vier handen",
        "klavier vierhandig",
        "vierhandig",
    ),
    "two pianos": ("2 pianos", "zwei klaviere"),
    "violin and piano": ("violine und klavier",),
    "viola and piano": ("bratsche und klavier",),
    "cello and piano": ("violoncello und klavier",),
    "flute and piano": ("flote und klavier",),
    "clarinet and piano": ("klarinette und klavier",),
    "voice and piano": ("singstimme und klavier", "gesang und klavier"),
    "mixed choir": ("mixed chorus", "gemischter chor", "satb"),
    "male choir": ("mannerchor",),
    "female choir": ("frauenchor",),
    "children's choir": ("childrens choir", "kinderchor"),
}

#: Categories that demonstrably include a smaller category, so that a work scored
#: for the whole still answers a question about the part. Deliberately shallow:
#: only what the name itself states. A string orchestra is not listed as
#: containing violins — it names an ensemble, and expanding it would put every
#: orchestral work in the answer to "works for violin".
CONTAINS: dict[str, tuple[str, ...]] = {
    "piano four hands": ("piano",),
    "two pianos": ("piano",),
    "violin and piano": ("violin", "piano"),
    "viola and piano": ("viola", "piano"),
    "cello and piano": ("cello", "piano"),
    "flute and piano": ("flute", "piano"),
    "clarinet and piano": ("clarinet", "piano"),
    "voice and piano": ("voice", "piano"),
    "piano trio": ("piano", "violin", "cello"),
    "piano quartet": ("piano", "violin", "viola", "cello"),
    "piano quintet": ("piano", "violin", "viola", "cello"),
    "string quartet": ("violin", "viola", "cello"),
    "string trio": ("violin", "viola", "cello"),
}

_SYNONYMS: dict[str, str] = {
    spelling: canonical for canonical, spellings in CATEGORIES.items() for spelling in (canonical, *spellings)
}

#: Umlauts folded rather than stripped, so "Flöte" reaches "flote" and not "flte".
_FOLDED = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss", "é": "e", "è": "e"})

_PUNCTUATION = re.compile(r"[^\w\s]+")
_SPACES = re.compile(r"\s+")
#: Words that qualify a scoring without changing which category it is.
_FILLER = frozenset({"for", "fur", "solo", "a", "an", "the", "part", "parts", "version"})
#: How a page joins the scorings of a small combination: "Violin and Piano",
#: "Singstimme und Klavier". Commas are deliberately *not* separators — an
#: orchestral scoring is a comma-separated list of sections ("flute, 2 oboes, …,
#: strings"), and splitting on it would read a symphony as a work for flute.
_SEPARATORS = re.compile(r"\band\b|\bund\b|\bwith\b|\bmit\b|&")


def _normalize(raw: str) -> str:
    """*raw* reduced to the form the tables are keyed on: folded, unpunctuated,
    and stripped of the words that qualify a scoring without naming one."""
    text = _PUNCTUATION.sub(" ", raw.casefold().translate(_FOLDED))
    words = [word for word in _SPACES.split(text) if word and word not in _FILLER]
    return " ".join(words)


def _category(text: str) -> str | None:
    """The canonical category *text* names, or ``None`` if the tables know none."""
    return _SYNONYMS.get(text)


def _expand(category: str) -> tuple[str, ...]:
    """*category* and the smaller categories it states it contains."""
    return (category, *CONTAINS.get(category, ()))


def _conjoined(text: str) -> tuple[str, ...]:
    """The categories of a scoring written as a conjunction, or nothing.

    All or nothing on purpose: a phrase is only read as a combination when *every*
    side of it is recognised. Half-understanding "piano and continuo" as a work for
    piano alone would be a claim the page did not make, and the ``orchestration``
    literal is already there to carry what could not be read.
    """
    parts = [_category(_normalize(part)) for part in _SEPARATORS.split(text)]
    if len(parts) < 2 or not all(parts):
        return ()
    return tuple(name for part in parts if part is not None for name in _expand(part))


def parse_instrumentation(raw: str) -> tuple[str, ...]:
    """The scoring categories *raw* states, broadest first.

    A recognised whole phrase leads, followed by whatever it contains
    (:data:`CONTAINS`); otherwise the phrase is read as a conjunction of scorings
    (see :func:`_conjoined`). Returns an empty tuple when nothing is recognised —
    the caller keeps the stated text as a literal either way and counts the miss so
    :data:`CATEGORIES` can grow.
    """
    text = _normalize(raw)
    if not text:
        return ()
    found = _expand(whole) if (whole := _category(text)) is not None else _conjoined(text)
    return tuple(dict.fromkeys(found))
