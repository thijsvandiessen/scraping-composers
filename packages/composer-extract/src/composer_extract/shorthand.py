"""Reading the orchestral shorthand a publisher's catalogue prints.

An orchestral work's scoring is not prose. Publishers print it as a positional
notation — sections separated by ``/`` or ``-``, each section a run of counts —
in two dialects that say the same thing:

===================================================== ===================
Chester/Novello (packed digits, ``/``)                 Boosey (dotted, ``-``)
===================================================== ===================
``3223 / 2230 / timp.perc / str[8]``                   ``3.2.2.3 - 2.2.3.0 - timp - strings[6]``
===================================================== ===================

Both read: 3 flutes, 2 oboes, 2 clarinets, 3 bassoons; 2 horns, 2 trumpets,
3 trombones, no tuba; timpani and percussion; strings. The first two sections are
*positional* — four counts standing for the four standing woodwind and brass desks,
in score order — and a parenthetical says how many of a desk's players double on
something else: ``3(pic)``, ``4(2pic)``, ``3(III=picc)``, ``4(III,IV=picc)`` and
``Dcl(=Ebcl)`` all occur.

What comes out is instruments, not a scoring category: a symphony is a work *for
orchestra* that *includes* a flute, which is why :mod:`.scoring` writes these as
``includes_instrument`` and only the ensemble itself as ``written_for``.

Detection is deliberately strict, because a false positive files a work under an
ensemble it was never written for: a shorthand must name strings *and* carry at
least one four-count section. Prose scoring never does, so it keeps going down
:func:`~.instrumentation.parse_instrumentation`'s path untouched.

Parsing is best-effort under that strict gate. A section no rule claims lands in
:attr:`Shorthand.unparsed` and contributes no instrument rather than a wrong one;
the caller counts it so the tables can grow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .instrumentation import category_for

#: The standing desks a positional section stands for, in score order. Their length
#: is also how many counts make a section positional at all.
_WOODWIND = ("flute", "oboe", "clarinet", "bassoon")
_BRASS = ("horn", "trumpet", "trombone", "tuba")
_DESKS = (_WOODWIND, _BRASS)
POSITIONS = len(_WOODWIND)

#: The category a shorthand has to name to be read as one.
_STRINGS = "strings"

#: Sections are separated by a slash or a dash *with space around it*: the dash
#: also occurs inside a token, and the dot is a separator only within a section.
_SPLIT = re.compile(r"\s+[/–—-]\s+")

#: One token: a count (with the parenthetical that may qualify it), a word (ditto),
#: or the bracketed number of string parts. Everything else — dots, slashes, plus
#: signs, spaces — only separates, so it needs no rule of its own.
_SCAN = re.compile(
    r"(?P<count>\d+)\s*(?:\((?P<cparen>[^)]*)\))?"
    r"|(?P<word>[A-Za-z]+)(?:\((?P<wparen>[^)]*)\))?"
    r"|\[(?P<parts>\d+)\]"
)

_ROMAN = re.compile(r"^[ivxlc]+(?:,[ivxlc]+)*$", re.I)
_LEADING_COUNT = re.compile(r"^(\d+)")


@dataclass(frozen=True)
class _Token:
    kind: str  # "count" | "word" | "parts"
    text: str
    paren: str | None


@dataclass(frozen=True)
class Shorthand:
    """What one shorthand says: which instruments, how many of each, and what it
    could not read.

    ``instruments`` is in score order (woodwind down through strings), which is the
    order the notation itself is written in. ``counts`` and ``string_parts`` are
    structure rather than facts to compare, so the caller stores them in the
    record's raw payload instead of turning them into claims.
    """

    instruments: tuple[str, ...]
    counts: dict[str, int]
    string_parts: int | None
    unparsed: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """The JSON-ready shape stored under a record's ``raw["scoring"]``."""
        data: dict[str, object] = {"instruments": list(self.instruments), "counts": dict(self.counts)}
        if self.string_parts is not None:
            data["string_parts"] = self.string_parts
        if self.unparsed:
            data["unparsed"] = list(self.unparsed)
        return data


def _scan(section: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _SCAN.finditer(section):
        if (count := match.group("count")) is not None:
            tokens.append(_Token("count", count, match.group("cparen")))
        elif (word := match.group("word")) is not None:
            tokens.append(_Token("word", word, match.group("wparen")))
        else:
            tokens.append(_Token("parts", match.group("parts"), None))
    return tokens


def _after(tokens: list[_Token], index: int) -> _Token | None:
    return tokens[index + 1] if index + 1 < len(tokens) else None


def _is_position(token: _Token, following: _Token | None) -> bool:
    """Whether *token* is one of a section's standing desks rather than a count of
    whatever is named after it.

    A count carrying a parenthetical is always a desk — the parenthetical is its
    doubling. A bare count is a desk unless a word follows it, in which case it
    counts that word: "5perc" and "2harps" are players, "3.2" are desks.
    """
    if token.kind != "count":
        return False
    return token.paren is not None or following is None or following.kind != "word"


def _unpack(tokens: list[_Token]) -> list[_Token]:
    """Chester's packed digits, split into one desk each.

    "3223" is four desks, not three thousand two hundred and twenty-three players;
    "5perc" and "12perc" are players, and keep their digits.
    """
    out: list[_Token] = []
    for index, token in enumerate(tokens):
        packed = (
            token.kind == "count"
            and token.paren is None
            and len(token.text) > 1
            and _is_position(token, _after(tokens, index))
        )
        if packed:
            out.extend(_Token("count", digit, None) for digit in token.text)
        else:
            out.append(token)
    return out


def _positions(tokens: list[_Token]) -> int:
    return sum(1 for index, token in enumerate(tokens) if _is_position(token, _after(tokens, index)))


def _add(found: dict[str, int], category: str | None, players: int) -> None:
    """Record *players* of *category*, if it is one and there are any.

    A zero count is a real statement — Mozart 25's "0.2.0.2" says there are no
    flutes — and says the instrument is absent, so it adds nothing.
    """
    if category and players:
        found[category] = found.get(category, 0) + players


def _paren_players(paren: str | None) -> int:
    """ "perc(4)" is four players; "Dcl(=Ebcl)" is one player who doubles."""
    return int(paren) if paren is not None and paren.isdigit() else 1


def _add_doubling(found: dict[str, int], paren: str | None) -> None:
    """The instrument a desk's players double on, and how many of them do.

    The count is a leading digit ("2pic"), a run of roman numerals naming which
    players ("III,IV=picc"), or one ("pic", "=Ebcl"). A digits-only parenthetical
    is a player count, not a doubling, and is handled by :func:`_paren_players`.
    """
    if paren is None or paren.isdigit():
        return
    who, _, what = paren.rpartition("=")
    if not what:
        return
    players = 1
    if (match := _LEADING_COUNT.match(what)) is not None:
        players, what = int(match.group(1)), what[match.end() :]
    elif who and _ROMAN.match(who):
        players = who.count(",") + 1
    _add(found, category_for(what), players)


def _read_desks(found: dict[str, int], tokens: list[_Token], desks: tuple[str, ...]) -> None:
    """A positional section: its counts are *desks* in score order, and anything
    else in it is an extra player the notation spells out ("+afl", "pictpt")."""
    desk = 0
    for index, token in enumerate(tokens):
        if _is_position(token, _after(tokens, index)):
            _add(found, desks[desk], int(token.text))
            _add_doubling(found, token.paren)
            desk += 1
        elif token.kind == "word":
            _add(found, category_for(token.text), _paren_players(token.paren))
            _add_doubling(found, token.paren)


def _read_tokens(found: dict[str, int], tokens: list[_Token]) -> tuple[bool, int | None]:
    """Any other section: named instruments, each optionally counted ("2harps",
    "5perc", "timp(2)"), plus the bracketed number of string parts.

    Returns whether anything in the section was understood, and the string-part
    count when it carried one.
    """
    parts: int | None = None
    pending = 1
    understood = False
    for token in tokens:
        if token.kind == "parts":
            parts, understood = int(token.text), True
        elif token.kind == "count":
            pending = int(token.text)
        else:
            if (category := category_for(token.text)) is not None:
                understood = True
            _add(found, category, pending * _paren_players(token.paren))
            _add_doubling(found, token.paren)
            pending = 1
    return understood, parts


def _names_strings(scanned: list[tuple[str, list[_Token]]]) -> bool:
    return any(
        token.kind == "word" and category_for(token.text) == _STRINGS
        for _, tokens in scanned
        for token in tokens
    )


def parse_shorthand(raw: str) -> Shorthand | None:
    """The instruments *raw* names, or ``None`` if it is not orchestral shorthand.

    ``None`` is the answer for everything that is not unmistakably this notation:
    the caller falls back to reading the text as prose, and the verbatim scoring
    survives as a literal either way.
    """
    scanned = [(part, _unpack(_scan(part))) for part in _SPLIT.split(raw.strip()) if part]
    positional = [tokens for _, tokens in scanned if _positions(tokens) == POSITIONS]
    if not positional or not _names_strings(scanned):
        return None
    found: dict[str, int] = {}
    unparsed: list[str] = []
    string_parts: int | None = None
    desks = 0
    for text, tokens in scanned:
        if desks < len(_DESKS) and _positions(tokens) == POSITIONS:
            _read_desks(found, tokens, _DESKS[desks])
            desks += 1
            continue
        understood, parts = _read_tokens(found, tokens)
        string_parts = parts if parts is not None else string_parts
        if not understood:
            unparsed.append(text)
    # The string section is a body of players, not one of them: how big it is is
    # what "strings[8]" says, so it carries no player count of its own.
    counts = {name: players for name, players in found.items() if name != _STRINGS}
    return Shorthand(tuple(found), counts, string_parts, tuple(unparsed))
