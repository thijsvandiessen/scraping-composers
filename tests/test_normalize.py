import pytest

from composer_ingest.normalize import dedup_key


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Beethoven, Ludwig van", "ludwig van beethoven"),  # comma-inverted -> natural order
        ("Ludwig van Beethoven", "ludwig van beethoven"),
        ("Dvořák, Antonín", "antonin dvorak"),  # diacritics stripped
        ("SAINT-SAËNS, Camille", "camille saintsaens"),  # case + punctuation folded
        ("  Mozart,   Wolfgang  Amadeus ", "wolfgang amadeus mozart"),  # whitespace collapsed
    ],
)
def test_dedup_key(name: str, expected: str) -> None:
    assert dedup_key(name) == expected


def test_different_people_keep_different_keys() -> None:
    assert dedup_key("Strauss, Johann") != dedup_key("Strauss, Richard")
