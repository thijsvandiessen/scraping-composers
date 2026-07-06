import uuid

import pytest
from composer_ingest.etl.normalize import dedup_key, entity_uuid


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


def test_entity_uuid_is_a_uuid() -> None:
    result = entity_uuid("person", "ludwig van beethoven")
    assert isinstance(result, uuid.UUID)


def test_entity_uuid_is_stable() -> None:
    # Same inputs must always produce the same UUID — the whole point of this function.
    assert entity_uuid("person", "ludwig van beethoven") == entity_uuid("person", "ludwig van beethoven")


def test_entity_uuid_differs_by_kind() -> None:
    assert entity_uuid("person", "composer") != entity_uuid("profession", "composer")


def test_entity_uuid_differs_by_key() -> None:
    assert entity_uuid("person", "ludwig van beethoven") != entity_uuid("person", "wolfgang amadeus mozart")


def test_dedup_key_empty_string() -> None:
    assert dedup_key("") == ""


def test_dedup_key_name_with_digits() -> None:
    # digits are \w and survive the punctuation-stripping step
    assert dedup_key("Philip II") == "philip ii"


def test_dedup_key_multiple_commas() -> None:
    # only the first comma triggers inversion; the remainder stays in the name
    assert dedup_key("Elgar, Edward, Sir") == "edward sir elgar"
