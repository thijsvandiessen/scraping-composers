"""Shared name/text cleanup for the NY Phil JSON.

Name fields need light parsing: whitespace runs collapse ("Beethoven,
Ludwig  van"), ``conductorName`` joins multiple conductors with ";" (rarely
``soloistName`` too, for dance troupes), and the "Not conducted" sentinel marks
works performed without a conductor rather than naming one.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# conductorName sentinel for works performed without a conductor
_NOT_CONDUCTED = "not conducted"

_WS = re.compile(r"\s+")


def _names(value: str | None) -> Iterator[str]:
    """Person names in a composerName/conductorName/soloistName value:
    ";"-separated, whitespace runs collapsed, empties and the "Not conducted"
    sentinel dropped."""
    for part in (value or "").split(";"):
        name = _WS.sub(" ", part).strip()
        if name and name.lower() != _NOT_CONDUCTED:
            yield name
