"""Small, dependency-free text helpers shared across the scraping tiers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

__all__ = ["collapse_ws", "distinct_ci", "strip_diacritics"]

_WS = re.compile(r"\s+")


def strip_diacritics(text: str) -> str:
    """Decompose *text* with NFKD and drop combining marks (``é`` → ``e``)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def collapse_ws(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip the ends."""
    return _WS.sub(" ", text).strip()


def distinct_ci(values: Iterable[str]) -> list[str]:
    """Distinct *values* in first-seen order, ignoring case."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out
