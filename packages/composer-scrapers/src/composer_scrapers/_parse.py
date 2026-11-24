"""Small shared parsing helpers for source scrapers."""

from __future__ import annotations

from typing import Any

__all__ = ["text"]


def text(value: Any) -> str | None:
    """A non-empty trimmed string, or None — APIs return both '' and null."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
