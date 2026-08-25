"""Tests for parsing person-name labels into structured parts."""

from composer_warehouse.persons.extract import parse_name


def test_comma_and_plain_forms_share_surname_and_given() -> None:
    a = parse_name("Beethoven, Ludwig van")
    b = parse_name("Ludwig van Beethoven")
    assert a.surname == b.surname == "beethoven"  # particle stripped from the key
    assert a.given == b.given == ("ludwig",)
    assert "van" in a.particles and "van" in b.particles


def test_initials_from_dotted_given_names() -> None:
    a = parse_name("Bach, J.S.")
    assert a.surname == "bach"
    assert a.given_initials == ("j", "s")  # "J.S." -> two initials

    b = parse_name("Bach, Johann Sebastian")
    assert b.given_initials == ("j", "s")


def test_surname_only_has_no_given_names() -> None:
    a = parse_name("Beethoven")
    assert a.surname == "beethoven"
    assert a.given == ()
    assert a.given_initials == ()


def test_von_particle_binds_to_surname_in_both_forms() -> None:
    a = parse_name("von Karajan, Herbert")
    b = parse_name("Herbert von Karajan")
    assert a.surname == b.surname == "karajan"
    assert a.given == b.given == ("herbert",)


def test_a_trailing_initial_is_not_the_surname() -> None:
    """ "Surname I." is a common form in Cyrillic and Hungarian sources.

    Reading the last token as the surname keyed these on the initial, so
    "Asafev B." and "Balash B." both landed in a bogus "b" block together.
    """
    name = parse_name("Asafev B.")
    assert (name.surname, name.given) == ("asafev", ("b",))

    two = parse_name("Asafev B. V.")
    assert (two.surname, two.given) == ("asafev", ("b", "v"))

    assert parse_name("Asafev B.").surname != parse_name("Balash B.").surname


def test_initials_leading_a_surname_still_read_as_given_names() -> None:
    name = parse_name("B. V. Asafev")
    assert (name.surname, name.given) == ("asafev", ("b", "v"))


def test_ordinary_names_are_unaffected() -> None:
    assert parse_name("Ludwig van Beethoven").surname == "beethoven"
    assert parse_name("Johann Sebastian Bach").surname == "bach"
