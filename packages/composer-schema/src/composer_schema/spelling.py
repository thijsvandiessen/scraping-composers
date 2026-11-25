"""Bracketed spellings: ``Bartok [Bartók], Béla`` is "Bartók, Béla".

The NY Phil archive catalogues a name in ASCII and writes its proper spelling
in brackets after the word(s) it respells. Kept verbatim, the name shares no
key with "Béla Bartók" anywhere else (#457), so the scraper reads it as the
bracketed spelling, and the warehouse does the same for every person label,
which also covers older bronze snapshots written before the scraper did.

A bracket counts as a respelling only when it is the word(s) before it with the
accents put back (Bartok/Bartók, Paert/Pärt, Mork/Mørk, Gungl/Gung'l). Any other
bracket is left where it is: another name (``Bucharoff [Buchalter], Simon``,
``Heller, Stephen [István]``), one that refers further back (``Herold, Louis
Joseph Ferdinand [Hérold]``) or an annotation (``Esther Baezner [-Vogel]``).
Which word such a bracket would replace cannot be told from the string.
"""

from __future__ import annotations

import re
import unicodedata

#: `` [Bartók]`` after the word(s) it may respell.
BRACKET = re.compile(r"\s*\[([^\[\]]*)\]")
_WS = re.compile(r"\s+")
#: Letters NFKD leaves alone.
_TRANSLIT = str.maketrans({"ø": "o", "Ø": "O", "ß": "ss", "đ": "d", "ł": "l", "æ": "ae"})
#: The umlauts German writes out (Pärt -> Paert).
_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def _fold(text: str) -> str:
    """Letters only, unaccented and casefolded: "Gung'l" -> "gungl"."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_TRANSLIT))
    return "".join(c for c in decomposed if c.isalpha() and not unicodedata.combining(c)).casefold()


def _respells(plain: str, spelled: str) -> bool:
    """Whether ``spelled`` is ``plain`` with its accents put back."""
    return bool(_fold(plain)) and _fold(plain) in {_fold(spelled), _fold(spelled.translate(_UMLAUT))}


def tidy(name: str) -> str:
    """Whitespace collapsed and none left before a comma ("André , José")."""
    return re.sub(r"\s+,", ",", _WS.sub(" ", name)).strip()


def apply_bracketed_spellings(name: str) -> str:
    """``name`` with every bracket that respells the word(s) before it applied.

    Brackets that are not respellings stay as they are (see the module docs).
    """
    if "[" not in name:
        return name
    out: list[str] = []
    pos = 0
    for match in BRACKET.finditer(name):
        before = name[pos : match.start()]
        words = before.split(" ")
        first = " ".join(match.group(1).split(",")[0].split())
        width = len(first.split())
        if 0 < width <= len(words) and _respells(" ".join(words[-width:]), first):
            out.append(" ".join([*words[:-width], first]))
        else:
            out.append(name[pos : match.end()])  # not a respelling: kept verbatim
        pos = match.end()
    out.append(name[pos:])
    return tidy("".join(out))
