"""Tests for the shared text helpers (:mod:`composer_schema.text`)."""

from __future__ import annotations

from composer_schema import collapse_ws, distinct_ci, strip_diacritics


def test_strip_diacritics_removes_combining_marks() -> None:
    assert strip_diacritics("Dvořák, Antonín") == "Dvorak, Antonin"
    assert strip_diacritics("Béla Bartók") == "Bela Bartok"
    assert strip_diacritics("plain ascii") == "plain ascii"


def test_collapse_ws_normalises_runs_and_trims() -> None:
    assert collapse_ws("  Ludwig\t\tvan\n  Beethoven  ") == "Ludwig van Beethoven"
    assert collapse_ws("   ") == ""
    assert collapse_ws("single") == "single"


def test_distinct_ci_preserves_first_seen_order_ignoring_case() -> None:
    assert distinct_ci(["Piano", "violin", "piano", "VIOLIN", "Cello"]) == ["Piano", "violin", "Cello"]
    assert distinct_ci([]) == []
    assert distinct_ci(iter(("Straße", "STRASSE"))) == ["Straße"]
