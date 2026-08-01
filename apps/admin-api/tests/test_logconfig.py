"""The log-line barrier: request-supplied values must not be able to forge entries."""

from __future__ import annotations

import pytest
from composer_admin.logconfig import FORMAT, safe_for_log


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("lso", "lso"),
        ("evil\nINFO forged", "evilINFO forged"),
        ("evil\r\nINFO forged", "evilINFO forged"),
        ("evil\rINFO forged", "evilINFO forged"),
        ("2026-07-02T09:52:30-3086f07d", "2026-07-02T09:52:30-3086f07d"),
    ],
)
def test_line_breaks_are_removed(value: str, expected: str) -> None:
    assert safe_for_log(value) == expected


def test_a_sanitized_value_cannot_add_a_line_to_the_log() -> None:
    """FORMAT writes one unstructured line per record, so the only thing standing
    between an interpolated value and a forged entry is the absence of a newline.
    """
    assert "%(message)s" in FORMAT
    forged = "lso\n2026-01-01 00:00:00 INFO     composer_admin: promoted to gold"

    assert "\n" not in safe_for_log(forged)
