import uuid

import pytest
from composer_models.normalize import (
    MAX_KEY_CHARS,
    QID_KEYED_KINDS,
    dedup_key,
    entity_key,
    entity_uuid,
    event_uuid,
    qid_key,
    wikidata_id,
)


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


def test_event_uuid_is_stable() -> None:
    # The public API serves this in /concerts/{id}; it must not drift between derives.
    assert event_uuid("concert", "nyphil", "1|1989-10-31") == event_uuid("concert", "nyphil", "1|1989-10-31")


@pytest.mark.parametrize(
    "other",
    [
        ("recording", "nyphil", "1|1989-10-31"),  # kind
        ("concert", "berlinphil", "1|1989-10-31"),  # source
        ("concert", "nyphil", "2|1989-10-31"),  # key
    ],
)
def test_event_uuid_differs(other: tuple[str, str, str]) -> None:
    assert event_uuid("concert", "nyphil", "1|1989-10-31") != event_uuid(*other)


def test_event_uuid_is_apart_from_entity_uuid() -> None:
    assert event_uuid("concert", "nyphil", "x") != entity_uuid("concert", "nyphil:x")


def test_dedup_key_empty_string() -> None:
    assert dedup_key("") == ""


def test_dedup_key_name_with_digits() -> None:
    # digits are \w and survive the punctuation-stripping step
    assert dedup_key("Philip II") == "philip ii"


def test_dedup_key_multiple_commas() -> None:
    # only the first comma triggers inversion; the remainder stays in the name
    assert dedup_key("Elgar, Edward, Sir") == "edward sir elgar"


def test_dedup_key_with_wikidata_id() -> None:
    assert dedup_key("Beethoven, Ludwig van", wikidata_id="Q255") == "ludwig van beethoven|Q255"
    assert dedup_key("Strauss, Johann", wikidata_id="Q72340") == "johann strauss|Q72340"
    assert dedup_key("Strauss, Johann", wikidata_id="Q312683") == "johann strauss|Q312683"


def test_dedup_key_is_bounded_for_the_postgres_btree_limit() -> None:
    # A scraper swallowing a whole index page produces names thousands of
    # characters long; the key lands in a unique btree index Postgres caps.
    key = dedup_key(" ".join(f"name{i}" for i in range(500)))
    assert len(key) == MAX_KEY_CHARS


def test_dedup_key_truncation_keeps_the_wikidata_suffix() -> None:
    # The id disambiguates two composers who share a name — truncating the
    # base must never cost it, or distinct people collapse into one entity.
    key = dedup_key("x" * 2000, wikidata_id="Q255")
    assert key.endswith("|Q255")
    assert len(key) == MAX_KEY_CHARS + len("|Q255")


@pytest.mark.parametrize(
    "name",
    ["Beethoven, Ludwig van", "x" * 2000, "Philip II", "Q255 the band"],
)
def test_wikidata_id_round_trips_the_dedup_key(name: str) -> None:
    # The QID is what tells the dedupe pass these are two distinct wikidata
    # items, so reading it back out has to survive whatever the base looks like.
    assert wikidata_id(dedup_key(name, wikidata_id="Q255")) == "Q255"


def test_wikidata_id_is_none_without_a_suffix() -> None:
    assert wikidata_id(dedup_key("Beethoven, Ludwig van")) is None


def test_wikidata_id_ignores_a_pipe_that_is_not_an_id() -> None:
    # dedup_key strips punctuation, so a bare pipe can only be the separator
    # this module wrote — but a caller may hand over any string.
    assert wikidata_id("johann strauss|junior") is None
    assert wikidata_id("johann strauss") is None


def test_qid_key() -> None:
    assert qid_key("Q215") == "wikidata:Q215"


def test_genres_and_movements_with_a_qid_are_keyed_on_it_alone() -> None:
    """A renamed Wikidata label must not split the genre in two (#349)."""
    assert QID_KEYED_KINDS == {"genre", "movement"}
    for kind in QID_KEYED_KINDS:
        assert entity_key(kind, "waltz", "Q215") == "wikidata:Q215"
        assert entity_key(kind, "Waltz (dance)", "Q215") == entity_key(kind, "waltz", "Q215")


def test_entity_key_falls_back_to_the_label_key() -> None:
    # a genre without a QID (a non-Wikidata source) keeps the label key
    assert entity_key("genre", "Symphonic Poem") == dedup_key("Symphonic Poem")
    # every other kind keeps dedup_key's label|QID form, which seeds person ids
    assert entity_key("person", "Beethoven, Ludwig van", "Q255") == "ludwig van beethoven|Q255"
    assert entity_key("place", "Bonn") == "bonn"


def test_wikidata_id_reads_both_key_forms() -> None:
    assert wikidata_id(qid_key("Q215")) == "Q215"
    assert wikidata_id(entity_key("genre", "waltz", "Q215")) == "Q215"
    assert wikidata_id(entity_key("person", "Beethoven, Ludwig van", "Q255")) == "Q255"
    # a label key can't spell the prefix: dedup_key strips the colon
    assert dedup_key("wikidata:Q215") == "wikidataq215"
    assert wikidata_id("wikidataq215") is None
