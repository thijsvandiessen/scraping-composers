"""Parse a person-name label into structured parts for matching.

Names arrive in two shapes: "Last, First Middle" (imslp/concertgebouw) and
"First Middle Last" (wikidata/orchestras). We split off the surname (folding on
any leading particle like ``van``/``von``/``de``) and keep the given names plus
their initials, so the matcher can compare "Bach, J.S." with "Bach, Johann
Sebastian" or "Beethoven" with "Beethoven, Ludwig van".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Lowercased nobiliary particles that bind to the surname rather than stand as
# given names. Kept small on purpose; extend as needed.
_PARTICLES = frozenset({"van", "von", "de", "del", "della", "di", "du", "la", "le", "ter", "ten", "der"})


@dataclass(frozen=True)
class PersonName:
    normalized: str
    surname: str
    given: tuple[str, ...]
    given_initials: tuple[str, ...]
    particles: tuple[str, ...]


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


def parse_name(label: str) -> PersonName:
    normalized = " ".join(_tokens(label))

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
    else:
        # "First Middle Last": the trailing token is the surname; particles right
        # before it bind to the surname but are dropped from the surname key.
        toks = _tokens(label)
        surname_tokens = toks[-1:]
        i = len(toks) - 1
        while i - 1 >= 0 and toks[i - 1] in _PARTICLES:
            i -= 1
        particles = tuple(toks[i : len(toks) - 1])
        given = tuple(t for t in toks[:i] if t not in _PARTICLES)

    return PersonName(
        normalized=normalized,
        surname=" ".join(surname_tokens),
        given=given,
        given_initials=_initials(given),
        particles=particles,
    )
