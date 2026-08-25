"""The label-based ensemble guard: what a credited name is allowed to be (#174)."""

import pytest
from composer_schema import looks_like_ensemble, resolve_entity_kind

# Every one of these reached the database as kind='person', from a participant
# credit on a concert or a recording.
ENSEMBLES = [
    "Malmö Symphony Orchestra",
    "Orchestre de Paris",
    "Gewandhausorchester Leipzig",
    "Koninklijk Concertgebouworkest",
    "Berliner Philharmoniker",
    "Netherlands Radio Philharmonic",
    "Britten Sinfonia",
    "London Sinfonietta",
    "Escher String Quartet",
    "Marcus Roberts Trio",
    "Berlin Radio Choir",
    "Chorus of Deutsche Oper Berlin",
    "MDR Rundfunkchor",
    "Tölzer Knabenchor",
    "Vocalconsort Berlin",
    "Cappella Amsterdam",
    "Staatskapelle Dresden",
    "I Musici",
]

MUSICIANS = [
    "Mozart, Wolfgang Amadeus",
    "Beinum, Eduard van",
    "Sir Simon Rattle",
    "Anne-Sophie Mutter",
    "Jean-Yves Thibaudet",
    "Ton Koopman",
    # Placeholder composers the sources report verbatim; deciding they are not
    # people is curation, and happens downstream — not here.
    "Anonymous,",
    "Traditional,",
]


@pytest.mark.parametrize("label", ENSEMBLES)
def test_ensemble_labels_are_recognised(label: str) -> None:
    assert looks_like_ensemble(label) is True


@pytest.mark.parametrize("label", MUSICIANS)
def test_musicians_are_left_alone(label: str) -> None:
    assert looks_like_ensemble(label) is False


def test_compound_head_needs_a_stem_in_front_of_it() -> None:
    """German writes the ensemble word onto the name; a surname only looks like
    it does. What keeps "Bachchor" apart from "Bachor" is how much word comes
    before the head."""
    assert looks_like_ensemble("Bachchor Mainz") is True
    assert looks_like_ensemble("Jan Bachor") is False


def test_conservative_by_design() -> None:
    """Two known misses, kept as misses on purpose.

    An ensemble whose name says nothing about being one is unreachable from the
    label, and a label fusing two credits is a parsing problem, not a kind
    problem (#174). Re-kinding a real person is the worse error of the two, so
    neither is chased with a looser rule.
    """
    assert looks_like_ensemble("Academy of St Martin in the Fields") is False
    assert looks_like_ensemble("Katia & Marielle Labèque") is False


def test_only_person_is_second_guessed() -> None:
    """A source that says "work" or "ensemble" fetched it under that heading;
    "person" is what a participant credit defaults to whether or not anyone
    looked."""
    assert resolve_entity_kind("person", "Malmö Symphony Orchestra") == "ensemble"
    assert resolve_entity_kind("person", "Mozart, Wolfgang Amadeus") == "person"
    assert resolve_entity_kind("ensemble", "Malmö Symphony Orchestra") == "ensemble"
    assert resolve_entity_kind("work", "Symphony No. 5") == "work"
    assert resolve_entity_kind("place", "Orchestra Hall") == "place"
