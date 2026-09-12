"""Turning a slice of laphil.com markup into the string it renders as."""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def text(fragment: str) -> str:
    """*fragment* with its tags dropped, entities resolved and runs of
    whitespace collapsed.

    Inner markup is routine here — a work title is wrapped in ``<em>``, a
    multi-role job title is joined with ``<br>`` — and non-breaking spaces are
    used for layout inside dates and names.
    """
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment)).replace("\xa0", " ")).strip()
