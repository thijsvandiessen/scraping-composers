"""Parse a person-name label into structured parts for matching.

Names arrive in two shapes: "Last, First Middle" (imslp/concertgebouw) and
"First Middle Last" (wikidata/orchestras). We split off the surname (folding on
any leading particle like ``van``/``von``/``de``) and keep the given names plus
their initials, so the matcher can compare "Bach, J.S." with "Bach, Johann
Sebastian" or "Beethoven" with "Beethoven, Ludwig van".

A plain label is ambiguous, though, and one parse cannot say so. "Ye Xiaogang"
is surname-first and "Xiaogang Ye" is not, but both read as "First Last"; "Adrian
Le Roy" and "Adrian Leroy" are one surname spelled two ways. So each parse also
carries its *alternate readings* (#201): the surname-first reading of a plain
name, and its surname with the spaces taken out. The matcher compares names
under every reading, which is where such a guess belongs. Folding it into the
dedup key instead would fuse the names at ingest, with no score and no way back.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

# Lowercased nobiliary particles that bind to the surname rather than stand as
# given names. Kept small on purpose; extend as needed.
_PARTICLES = frozenset({"van", "von", "de", "del", "della", "di", "du", "la", "le", "ter", "ten", "der"})

# Generational suffixes. They tell namesakes apart ("Johann Strauss II" is not
# his father), so no alternate reading may treat one as a name part: a label
# carrying one gets no alternates at all (see :func:`parse_name`).
_SUFFIXES = frozenset({"i", "ii", "iii", "sr", "snr", "jr", "jun", "sen"})


PRIMARY = "primary"
"""The label read the way :func:`parse_name` always has."""

REORDERED = "reordered"
"""A plain label read surname-first: "Ye Xiaogang" as surname ``ye``."""

COLLAPSED = "collapsed"
"""The surname with its spaces taken out: "Adrian Le Roy" as ``leroy``."""


@dataclass(frozen=True)
class PersonName:
    normalized: str
    surname: str
    given: tuple[str, ...]
    given_initials: tuple[str, ...]
    particles: tuple[str, ...]
    # Which reading of the label this is, and (on the primary only) the other
    # readings. Neither takes part in equality: two labels that parse to the
    # same parts are the same name, however their alternates differ.
    reading: str = field(default=PRIMARY, compare=False)
    alternates: tuple[PersonName, ...] = field(default=(), compare=False)

    def readings(self) -> tuple[PersonName, ...]:
        """This reading first, then every alternate one."""
        return (self, *self.alternates)


def comparable(a: PersonName, b: PersonName) -> bool:
    """Whether two readings agree on a surname, and so can be compared at all.

    A surname-first reading is held to more than the surname, because it is a
    guess that turns a given name into the surname. Two of them never qualify:
    their agreeing says the two people share a first name ("John Smith" and
    "John Williams" under ``john``). Against a reading that takes the surname
    where it was written, the given names must also start with the same
    spelled-out name. Measured on the live corpus, a shared surname alone let
    "Georges Alary" read surname-first meet the alias "A. Georges" on the
    initial, and auto-link two different men on a birth year.
    """
    if not a.surname or a.surname != b.surname:
        return False
    if REORDERED not in (a.reading, b.reading):
        return True
    if a.reading == b.reading:
        return False
    return bool(a.given) and bool(b.given) and a.given[0] == b.given[0]


def _strip_diacritics(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _tokens(text: str) -> list[str]:
    """Lowercased, diacritic-stripped word tokens (punctuation dropped)."""
    text = _strip_diacritics(text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip().split()


def _initials(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """First letters of the given names. A short run-together token (e.g. "js"
    from "JS") expands to one initial per letter; "J.S." already splits into
    separate tokens once punctuation is dropped."""
    out: list[str] = []
    for tok in tokens:
        if len(tok) <= 2 and tok.isalpha():
            out.extend(tok)
        else:
            out.append(tok[0])
    return tuple(out)


def _is_surname_first(tokens: list[str]) -> bool:
    """True for the "Surname I. I." form: a name whose tokens after the first
    are all single letters, and so are initials rather than name parts."""
    return len(tokens) > 1 and all(len(token) == 1 for token in tokens[1:])


def _name(
    normalized: str,
    surname: str,
    given: tuple[str, ...],
    particles: tuple[str, ...],
    reading: str = PRIMARY,
) -> PersonName:
    return PersonName(
        normalized=normalized,
        surname=surname,
        given=given,
        given_initials=_initials(given),
        particles=particles,
        reading=reading,
    )


def _surname_first(normalized: str, toks: list[str]) -> PersonName | None:
    """A two-word plain label read surname-first, as Chinese, Hungarian and
    many library sources write names without a comma: "Ye Xiaogang", "Bartók
    Béla". Leading particles bind to the surname, as they do at the end.

    Longer names are left alone. Read surname-first, "Michael George Giacchino"
    becomes given names "george giacchino", which "George Michael" is a prefix
    of; with only one word left over there is no such second guess to make.
    """
    i = 0
    while i < len(toks) and toks[i] in _PARTICLES:
        i += 1
    if len(toks) - i != 2 or len(toks[i]) < 2 or toks[i + 1] in _PARTICLES:
        return None  # not surname + one given name, or an initial in its place
    return _name(normalized, toks[i], (toks[i + 1],), tuple(toks[:i]), REORDERED)


def _collapsed(
    normalized: str, surname_tokens: list[str], given: tuple[str, ...], particles: tuple[str, ...]
) -> PersonName | None:
    """The reading whose surname is ``surname_tokens`` written as one word."""
    if len(surname_tokens) < 2:
        return None
    return _name(normalized, "".join(surname_tokens), given, particles, COLLAPSED)


def parse_name(label: str) -> PersonName:
    """The primary reading of ``label``, carrying its alternates."""
    normalized = " ".join(_tokens(label))
    alternates: list[PersonName | None] = []

    if "," in label:
        # "Last, First Middle": everything before the comma is the surname. The
        # comma-inverted form trails particles after the given names
        # ("Beethoven, Ludwig van"), so strip particles from both sides and the
        # core surname stays the same as the plain form.
        before, _, after = label.partition(",")
        before_toks, after_toks = _tokens(before), _tokens(after)
        surname_tokens = [t for t in before_toks if t not in _PARTICLES]
        given = tuple(t for t in after_toks if t not in _PARTICLES)
        particles = tuple(t for t in (*before_toks, *after_toks) if t in _PARTICLES)
        # "Backer-Grøndahl, Agathe": the comma says where the surname is, so
        # the only doubt left is whether it is one word or several.
        alternates.append(_collapsed(normalized, before_toks, given, ()))
    elif _is_surname_first(toks := _tokens(label)):
        # "Surname I. I." — the trailing tokens are all initials, so the
        # surname leads. Taking the last token instead made the *initial* the
        # surname: "Асафьев Б." and "Балаш Б." both keyed on "б", which blocked
        # two unrelated people into the same candidate group.
        surname_tokens, given, particles = toks[:1], tuple(toks[1:]), ()
    else:
        # "First Middle Last": the trailing token is the surname; particles right
        # before it bind to the surname but are dropped from the surname key.
        surname_tokens = toks[-1:]
        i = len(toks) - 1
        while i - 1 >= 0 and toks[i - 1] in _PARTICLES:
            i -= 1
        particles = tuple(toks[i : len(toks) - 1])
        given = tuple(t for t in toks[:i] if t not in _PARTICLES)
        alternates.append(_surname_first(normalized, toks))
        if particles:
            # "Adrian Le Roy" / "Adrian Leroy": the particle may be part of
            # the surname's spelling rather than a word before it.
            alternates.append(_collapsed(normalized, toks[i:], given, ()))
        elif len(given) >= 2:
            # "Agathe Backer Grøndahl" / "Agathe Backergrøndahl": the last two
            # words may be one double-barrelled surname.
            alternates.append(_collapsed(normalized, toks[i - 1 :], given[:-1], ()))

    surname = " ".join(surname_tokens)
    if _SUFFIXES.intersection(_tokens(label)):
        # Every alternate would either read the suffix as a name ("straussii",
        # given "ii") or drop it, and dropping it is worse: "Johann Strauss II"
        # read surname-first is plain "Strauss Johann", which any of the three
        # Johann Strausses could be. Readings exist to find more matches, and a
        # namesake family is exactly where more matches are wrong.
        alternates = []
    return replace(
        _name(normalized, surname, given, particles),
        alternates=tuple(alt for alt in alternates if alt is not None and alt.surname != surname),
    )
