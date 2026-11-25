"""Shared name/text cleanup for the NY Phil JSON.

Name fields need light parsing: whitespace runs collapse ("Beethoven,
Ludwig  van"), ``conductorName`` joins multiple conductors with ";" (rarely
``soloistName`` too, for dance troupes), and the "Not conducted" sentinel marks
works performed without a conductor rather than naming one.

The archive also writes a name's proper spelling in brackets after the ASCII
one it was catalogued under: ``Bartok [Bartók], Béla``. :func:`spelling`
reads it as the bracketed spelling (see :mod:`composer_schema.spelling`) and
keeps the ASCII one as an alias.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from composer_schema.spelling import BRACKET, apply_bracketed_spellings, tidy

# conductorName sentinel for works performed without a conductor
_NOT_CONDUCTED = "not conducted"

WS = re.compile(r"\s+")
_WS = WS


def spelling(name: str) -> tuple[str, tuple[str, ...]]:
    """``name`` with its bracketed spellings applied, and the alias it implies.

    ``Bartok [Bartók], Béla`` is "Bartók, Béla", alias "Bartok, Béla". A bracket
    that is not a respelling (see :mod:`composer_schema.spelling`) is dropped
    from the name and yields no alias: which word it would replace cannot be
    told from the string.
    """
    if "[" not in name:
        return name, ()
    result = tidy(BRACKET.sub("", apply_bracketed_spellings(name)))
    plain = tidy(BRACKET.sub("", name))
    return result, (plain,) if plain != result else ()


def names(value: str | None) -> Iterator[str]:
    """Person names in a composerName/conductorName/soloistName value:
    ";"-separated, whitespace runs collapsed, bracketed spellings applied
    (:func:`spelling`), empties and the "Not conducted" sentinel dropped."""
    for name, _ in spellings(value):
        yield name


def spellings(value: str | None) -> Iterator[tuple[str, tuple[str, ...]]]:
    """:func:`names` with each name's aliases (see :func:`spelling`)."""
    for part in (value or "").split(";"):
        name = WS.sub(" ", part).strip()
        if name and name.lower() != _NOT_CONDUCTED:
            yield spelling(name)


_names = names
