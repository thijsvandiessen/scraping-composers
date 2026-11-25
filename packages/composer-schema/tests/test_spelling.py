"""Bracketed spellings: ``Bartok [Bartók], Béla`` is "Bartók, Béla" (#457)."""

import pytest
from composer_schema.spelling import apply_bracketed_spellings


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Hill, Ureli Corelli", "Hill, Ureli Corelli"),
        ("Bartok [Bartók], Béla", "Bartók, Béla"),
        ("Furtwangler [Furtwängler], Wilhelm", "Furtwängler, Wilhelm"),
        ("Eletfa [Életfa] Hungarian Folk Band", "Életfa Hungarian Folk Band"),
        # several brackets, a multi-word one, and the stray space before a comma
        ("Gretry [Grétry], Andre [André] Ernest Modeste", "Grétry, André Ernest Modeste"),
        ("Du Pre [Du Pré], Jacqueline", "Du Pré, Jacqueline"),
        ("Andre [André] , José", "André, José"),
        ("Willis-Sorensen [Willis-Sørensen], Rachel", "Willis-Sørensen, Rachel"),
        # transliterations NFKD alone does not fold, and punctuation
        ("Paert [Pärt], Arvo", "Pärt, Arvo"),
        ("Baer [Bär], Olaf", "Bär, Olaf"),
        ("Mork [Mørk], Truls", "Mørk, Truls"),
        ("Gungl [Gung'l], Joseph", "Gung'l, Joseph"),
        # not a respelling: left where it is
        ("Bucharoff [Buchalter], Simon", "Bucharoff [Buchalter], Simon"),
        ("Heller, Stephen [István]", "Heller, Stephen [István]"),
        ("Carrer [Karrer, Karreris], Pavlos", "Carrer [Karrer, Karreris], Pavlos"),
        ("Herold, Louis Joseph Ferdinand [Hérold]", "Herold, Louis Joseph Ferdinand [Hérold]"),
        ("Kurtag [Kurtág], Georgy [György]", "Kurtág, Georgy [György]"),
        ("Esther Baezner [-Vogel]", "Esther Baezner [-Vogel]"),
        ("[Senfl, Ludwig]", "[Senfl, Ludwig]"),
        ("[sic]", "[sic]"),
    ],
)
def test_apply_bracketed_spellings(name: str, expected: str) -> None:
    assert apply_bracketed_spellings(name) == expected
