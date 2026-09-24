"""Turning stated scoring into instrumentation entities you can query.

A page writes its scoring for people to read — "Klavier zu vier Händen", "for
string orchestra", "Violine und Klavier" — and that text is kept verbatim as an
``orchestration`` literal. A literal answers no question, though: "every work for
piano" over free prose is a ``LIKE`` across a dozen spellings in two languages.

So the stated text is also folded onto a canonical *scoring category* here, and
:mod:`composer_extract.scoring` emits one ``written_for`` claim per category, pointing at an
``instrumentation`` entity. The category — not the individual instrument — is the
unit, because that is what the catalogues themselves are organised by: Bärenreiter
offers "works for string orchestra" as a facet, and a string orchestra is not a
list of instruments any more than a piano is.

Two tables expand a category, and which one a category belongs in decides which
predicate its parts land on. :data:`CONTAINS` holds the categories that name
instruments — a violin sonata really is *for* the piano, so it still answers "works
for piano". :data:`MEMBERS` holds the ones that name an ensemble, whose instruments
the work merely *includes*; :mod:`.shorthand` draws the same line for an orchestra.

Nothing here guesses, on the same principle as :mod:`composer_extract.values`: a phrase the tables
do not recognise yields no category at all rather than an invented one. The
``orchestration`` literal still carries it, and the run log counts it (see
:meth:`~.resilience.ExtractStats.unrecognised_summary`) so the tables can grow.

"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Canonical scoring category -> the spellings that denote it. Inverted into
#: :data:`_SYNONYMS` below; written this way round because that is how it is read
#: and maintained. German spellings are carried because both catalogues this was
#: grown against serve the same pages in two languages.
#:
#: The spellings include the abbreviations a publisher's orchestral shorthand uses
#: ("pic", "timp", "str", "corA"), so :mod:`.shorthand` resolves its tokens through
#: this one table rather than a second one that could drift from it. They are terse
#: enough to be ambiguous in prose, but a lookup is on a whole normalized string, so
#: only a scoring reading exactly "fl" matches.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "piano": ("klavier", "pianoforte", "pf", "pno", "klav"),
    "fortepiano": ("hammerklavier",),
    "violin": ("violine", "geige", "vl", "vn", "vln"),
    "viola": ("bratsche", "va", "vla"),
    "viola d'amore": ("viola d amore",),
    "viola da gamba": ("gambe", "gamba"),
    "violone": (),
    "cello": ("violoncello", "violoncell", "vc"),
    "double bass": ("contrabass", "kontrabass", "doublebass", "double-bass", "db", "kb"),
    "flute": ("flote", "floete", "querflote", "fl", "western concert flute"),
    "piccolo": ("pic", "picc", "pikkoloflote", "piccolo flute"),
    "alto flute": ("afl", "altflote"),
    "oboe": ("ob",),
    "oboe d'amore": ("oboe d amore", "oboe damore"),
    "oboe da caccia": (),
    "english horn": ("cor anglais", "ca", "cora", "corang", "englischhorn", "eh", "englhn"),
    "clarinet": ("klarinette", "cl", "klar"),
    "bass clarinet": ("bcl", "bassklarinette", "bklar", "bkl", "klar b"),
    "e-flat clarinet": ("ebcl", "esklar", "es klar"),
    "d clarinet": ("dcl",),
    "basset horn": ("bassetthorn", "bassethorn"),
    "bassoon": ("fagott", "bn", "bsn", "fag", "fg"),
    "contrabassoon": ("dbn", "cbn", "cbsn", "kontrafagott", "kfag", "kfg"),
    "saxophone": ("sax", "saxophon"),
    "soprano saxophone": ("sopransaxophon",),
    "alto saxophone": ("altsaxophon",),
    "tenor saxophone": ("tenorsaxophon",),
    "baritone saxophone": ("baritonsaxophon",),
    "horn": ("french horn", "waldhorn", "hn", "corno da caccia"),
    "tenor tuba": ("ttuba",),
    # "Clarino" is the Baroque name for the trumpet's high register and its part.
    "trumpet": ("trompete", "tpt", "clarino", "trp"),
    "cornet": ("kornett",),
    "bass trumpet": ("btpt",),
    "piccolo trumpet": ("pictpt", "picctpt"),
    "trombone": ("posaune", "tbn", "tenor trombone", "tenorposaune", "pos"),
    "bass trombone": ("btbn", "bpos"),
    "tuba": ("tba", "tb", "tub"),
    "euphonium": (),
    "harp": ("harfe", "hp", "harps", "hfe", "hf", "hrf"),
    "guitar": ("gitarre", "git"),
    "bass guitar": ("e bass", "bassgitarre"),
    "lute": ("laute",),
    "mandolin": ("mandoline",),
    "accordion": ("akkordeon", "akk"),
    "organ": ("orgel", "org"),
    "harmonium": ("harm",),
    "harpsichord": ("cembalo", "hpd", "cemb"),
    "clavichord": (),
    # A generic keyboard part ("Tasteninstrument"): the page names no instrument.
    "keyboard": ("keyboard instrument", "tasteninstrument"),
    # A part any melody instrument may take ("Melodieinstrument"): the page names
    # the role, not the instrument.
    "melody instrument": ("melodieinstrument", "melodic instrument"),
    # The bass line and its realisation; the instruments that play it are stated
    # alongside when the page knows them ("Basso continuo (Violoncello, Organ)").
    "basso continuo": ("continuo", "bc", "b c", "cnt", "generalbass"),
    "celesta": ("cel", "celeste"),
    "recorder": ("blockflote",),
    "descant recorder": ("soprano recorder", "sopran blockflote", "sopranblockflote"),
    "treble recorder": ("alto recorder", "alt blockflote", "altblockflote"),
    "tenor recorder": ("tenor blockflote", "tenorblockflote"),
    "bass recorder": ("bass blockflote", "bassblockflote"),
    "percussion": ("schlagzeug", "schlagwerk", "perc", "perk", "schlg", "schlzg", "schlz", "schl"),
    "timpani": ("timp", "tmp", "pauken", "pauke", "kettledrums", "pk"),
    "crotales": ("crot",),
    "cymbals": ("cyms", "cym", "becken"),
    "tam-tam": ("tam", "tamtam"),
    "triangle": ("tgl", "triangel"),
    "bass drum": ("bd", "grosse trommel", "concert bass drum"),
    "snare drum": ("sd", "side drum", "kleine trommel"),
    "tambourine": ("tamb", "tambourin"),
    "tape": ("tonband",),
    # Unpitched "drum(s)"/"Trommel", where the page does not say which.
    "drum": ("drums", "trommel"),
    "glockenspiel": ("glock",),
    "xylophone": ("xyl", "xylophon"),
    "vibraphone": ("vib", "vibraphon"),
    "marimba": ("mar",),
    "guiro": (),
    "voice": (
        "singstimme",
        "gesang",
        "vocal",
        "voices",
        "singing voice",
        # "Solo singer": the filler word "solo" is dropped before lookup.
        "singer",
        # A song's range, not a voice type: all three are sung by any voice
        # that has it, so they are one category.
        "high voice",
        "hohe stimme",
        "medium voice",
        "mittlere stimme",
        "low voice",
        "tiefe stimme",
    ),
    "soprano": ("sopran",),
    "mezzo-soprano": ("mezzo", "mezzosopran"),
    "alto": ("alt",),
    "tenor": (),
    "baritone": ("bariton",),
    "bass": (),
    "speaker": ("sprecher", "narrator", "erzahler"),
    # Vocal soloists, number and voices unstated ("Soloists, Choir, Orchestra").
    "soloists": ("soli", "solisten"),
    "orchestra": (
        "orchester",
        "symphony orchestra",
        "sinfonieorchester",
        "grosses orchester",
        "full orchestra",
    ),
    # Distinct from "string orchestra", and the reason "strings"/"Streicher" is
    # not a spelling of it: in an orchestral scoring those name the string
    # *section* of a full orchestra, not a work for string orchestra.
    "strings": ("str", "streicher", "cordes", "archi"),
    "string orchestra": ("streichorchester",),
    "chamber orchestra": ("kammerorchester",),
    "wind ensemble": ("wind band", "wind orchestra", "blasorchester", "blaserensemble"),
    # Sections named as such in a scoring list, like "strings" above.
    "winds": ("blaser", "wind instruments"),
    "brass": ("blechblaser", "brass instruments"),
    "string quartet": ("streichquartett", "string quartets"),
    "string trio": ("streichtrio", "string trios"),
    "string quintet": ("streichquintett", "string quintets"),
    "string sextet": ("streichsextett", "string sextets"),
    "saxophone quartet": ("saxophonquartett",),
    "piano trio": ("klaviertrio", "piano trios"),
    "piano quartet": ("klavierquartett", "piano quartets"),
    "piano quintet": ("klavierquintett", "piano quintets"),
    "wind quintet": ("blaserquintett", "wind quintets"),
    "piano four hands": (
        "piano 4 hands",
        "klavier 4 handig",
        "four hands",
        "piano duet",
        "klavier zu vier handen",
        "klavier vierhandig",
        "vierhandig",
    ),
    "piano six hands": ("piano 6 hands", "klavier 6 handig", "klavier sechshandig"),
    "two pianos": ("2 pianos", "zwei klaviere", "2 pianos 4 hands"),
    "violin and piano": ("violine und klavier",),
    "viola and piano": ("bratsche und klavier",),
    "cello and piano": ("violoncello und klavier",),
    "flute and piano": ("flote und klavier",),
    "clarinet and piano": ("klarinette und klavier",),
    "voice and piano": ("singstimme und klavier", "gesang und klavier"),
    "choir": ("chorus", "chor"),
    "mixed choir": ("mixed chorus", "gemischter chor", "satb"),
    "chamber choir": ("kammerchor",),
    # Upper or lower voices only, the choir's gender unstated ("Gleiche Stimmen").
    "equal voices": ("gleiche stimmen",),
    "male choir": ("mannerchor",),
    "female choir": ("frauenchor",),
    "children's choir": ("childrens choir", "kinderchor"),
}

#: Categories that name instruments rather than an ensemble, and the instruments
#: they name. A work scored for these really is *for* each of them — a violin
#: sonata is for the piano as much as for the violin — so these expand into further
#: ``written_for``.
CONTAINS: dict[str, tuple[str, ...]] = {
    "piano four hands": ("piano",),
    "piano six hands": ("piano",),
    "two pianos": ("piano",),
    "violin and piano": ("violin", "piano"),
    "viola and piano": ("viola", "piano"),
    "cello and piano": ("cello", "piano"),
    "flute and piano": ("flute", "piano"),
    "clarinet and piano": ("clarinet", "piano"),
    "voice and piano": ("voice", "piano"),
}

#: Categories that name an *ensemble*, and the instruments that ensemble is made
#: of. A string quartet is what the work is for; the violin is a member of it. So
#: these expand into ``includes_instrument`` instead — the same distinction
#: :mod:`.shorthand` draws for an orchestra, and the reason "works for violin" does
#: not return every quartet ever written.
#:
#: Deliberately shallow: only what the name itself states. "String orchestra" and
#: "orchestra" name no fixed instruments and are listed nowhere here.
MEMBERS: dict[str, tuple[str, ...]] = {
    "piano trio": ("piano", "violin", "cello"),
    "piano quartet": ("piano", "violin", "viola", "cello"),
    "piano quintet": ("piano", "violin", "viola", "cello"),
    "string quartet": ("violin", "viola", "cello"),
    "string trio": ("violin", "viola", "cello"),
    "string sextet": ("violin", "viola", "cello"),
    "wind quintet": ("flute", "oboe", "clarinet", "bassoon", "horn"),
    "saxophone quartet": ("soprano saxophone", "alto saxophone", "tenor saxophone", "baritone saxophone"),
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


#: Keyed on the *normalized* spelling, because that is what a lookup holds: a key
#: written with punctuation ("tam-tam", "children's choir") would otherwise be
#: unreachable, since :func:`_normalize` has already turned the hyphen into a space.
_SYNONYMS: dict[str, str] = {
    _normalize(spelling): canonical
    for canonical, spellings in CATEGORIES.items()
    for spelling in (canonical, *spellings)
}


def category_for(raw: str) -> str | None:
    """The canonical category *raw* names, or ``None`` if the table knows none.

    Normalizes first, so "Klavier", "PIANO solo" and " pf " all arrive at the same
    entry. :mod:`.shorthand` resolves its abbreviations through here, which is what
    keeps one table behind both the prose and the shorthand path.
    """
    return _SYNONYMS.get(_normalize(raw))


def _category(text: str) -> str | None:
    """As :func:`category_for`, for text this module has already normalized."""
    return _SYNONYMS.get(text)


def _expand(category: str) -> tuple[str, ...]:
    """*category* and the instruments it names, all of which the work is for."""
    return (category, *CONTAINS.get(category, ()))


def members_of(categories: Iterable[str]) -> tuple[str, ...]:
    """The instruments the named ensembles among *categories* are made of.

    Kept apart from :func:`parse_instrumentation`'s answer because these are what a
    work *includes*, not what it is *for* (see :data:`MEMBERS`).
    """
    found: list[str] = [name for category in categories for name in MEMBERS.get(category, ())]
    return tuple(dict.fromkeys(found))


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
