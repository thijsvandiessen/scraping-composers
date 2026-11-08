from __future__ import annotations

import pytest
from composer_schema import SourceClaim
from composer_schema.ontology import (
    LITERAL,
    PREDICATES,
    Theme,
    UnknownPredicateError,
    check_claim,
    predicates_in,
)


def test_every_predicate_has_a_meaning_and_an_object() -> None:
    for name, entry in PREDICATES.items():
        assert entry.name == name
        assert entry.meaning.strip(), name
        assert entry.objects, name


def test_names_are_unique_snake_case() -> None:
    from composer_schema.ontology import _VOCABULARY  # pyright: ignore[reportPrivateUsage]

    assert len(PREDICATES) == len(_VOCABULARY)
    for name in PREDICATES:
        assert name == name.lower() and " " not in name and "-" not in name, name


def test_every_theme_is_used_and_themes_partition_the_vocabulary() -> None:
    themed = [predicates_in(theme) for theme in Theme]
    assert all(themed)
    assert sum(len(names) for names in themed) == len(PREDICATES)
    assert frozenset().union(*themed) == frozenset(PREDICATES)


def test_relation_theme_is_person_to_person() -> None:
    for name in predicates_in(Theme.RELATION):
        assert PREDICATES[name].objects == {"person"}, name


def test_a_listed_claim_passes() -> None:
    check_claim(SourceClaim("born_on", value="1756-01-27"))
    check_claim(SourceClaim("student_of", "person", "Salieri, Antonio"))
    check_claim(SourceClaim("has_genre", "genre", "opera"))
    check_claim(SourceClaim("has_genre", value="Symphonies"))


def test_an_unlisted_predicate_is_rejected() -> None:
    with pytest.raises(UnknownPredicateError, match="born_at"):
        check_claim(SourceClaim("born_at", value="1756"))


def test_the_wrong_object_kind_is_rejected() -> None:
    with pytest.raises(UnknownPredicateError, match="literal"):
        check_claim(SourceClaim("student_of", value="Salieri"))
    with pytest.raises(UnknownPredicateError, match="place"):
        check_claim(SourceClaim("born_on", "place", "Salzburg"))
    assert LITERAL not in PREDICATES["born_in"].objects
