"""Turning a slice of harmoniamundi.com markup into the string it renders as.

Two things make this more than a tag strip.

**Tags are dropped, not spaced.** The usual trick of replacing each tag with a
space breaks titles here, because the site's editors bold a fragment of one:
``<b>Fantasia in D Minor</b>, K.397`` would come back as "Fantasia in D Minor ,
K.397". Every tag inside the fields this source reads is inline formatting
(``b``, ``strong``, ``em``, ``i``, ``small``, ``sup``, ``a``, ``svg``) whose own
text already carries the spaces it needs, so removing them outright reproduces
the rendered string exactly. Anything line-level is turned into a newline by
:func:`lines` *before* this runs.

**Line breaks come two ways.** The tracklist blob is a hand-edited WYSIWYG
field, and the editor has changed over the catalogue's life: older albums
separate lines with ``<br>``, newer ones with literal newlines in the source,
and some mix both. :func:`lines` treats the two alike, which is the only reason
one line classifier works across the whole catalogue.
"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

#: Markup that renders as a line break. ``<p>`` and ``<div>`` are included for
#: the biography field, which is paragraphs rather than ``<br>``-separated lines.
_BREAK_RE = re.compile(r"<br\s*/?>|</?p\b[^>]*>|</?div\b[^>]*>", re.IGNORECASE)


def text(fragment: str) -> str:
    """*fragment* with its tags removed, entities resolved and whitespace collapsed.

    Non-breaking spaces are used for layout throughout — inside work titles
    (``<strong>Chout\xa0</strong>Op. 21``) and between a bullet and its track —
    so they are folded into ordinary spaces rather than left to surprise a
    caller comparing strings.
    """
    stripped = html.unescape(_TAG_RE.sub("", fragment))
    return _WS_RE.sub(" ", stripped.replace("\xa0", " ")).strip()


def lines(fragment: str) -> list[str]:
    """*fragment* as the non-empty lines it renders as.

    Both break styles are honoured (see the module docstring), and each line is
    run through :func:`text`, so a caller sees the same strings a reader does.
    """
    return [line for line in (text(part) for part in _BREAK_RE.sub("\n", fragment).split("\n")) if line]
