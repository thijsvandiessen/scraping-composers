"""Catalogue numbers normalised per composer (#351)."""

import pytest
from composer_schema.catalogues import (
    composer_catalogues,
    find_catalogues,
    normalize_catalogue,
    parse_catalogue,
)

MOZART = "Mozart, Wolfgang Amadeus"
SCARLATTI = "Scarlatti, Domenico"


def pairs(text: str, composer: str | None = None) -> list[tuple[str, str]]:
    return [(ref.scheme, ref.value) for ref in find_catalogues(text, composer)]


@pytest.mark.parametrize(
    "title",
    [
        "Symphony no. 40 in G minor K. 550",
        "Sinfonie in g KV 550 (1. Fassung)",
        "Symfonie nr. 40 in g kl.t., KV 550",
        "Symphony No. 40 (K.550)",
    ],
)
def test_k_and_kv_are_both_koechel_for_mozart(title: str) -> None:
    assert pairs(title, MOZART) == [("KV", "550")]


def test_a_lettered_koechel_number_is_the_sixth_edition() -> None:
    assert pairs("Symphony No. 25, K. 173dB", MOZART) == [("KV6", "173db")]
    assert pairs("Symphony No. 25, K. 183/173dB", MOZART) == [("KV", "183"), ("KV6", "173db")]


def test_kv_is_koechel_even_without_a_composer() -> None:
    assert pairs("Rondo KV 485") == [("KV", "485")]


def test_k_is_kirkpatrick_for_domenico_scarlatti() -> None:
    assert pairs("Sonata in D minor, K. 9, L. 413", SCARLATTI) == [("Kk", "9"), ("L", "413")]
    assert pairs("Sonata, Kk. 9", "Domenico Scarlatti") == [("Kk", "9")]


def test_a_relative_with_a_catalogue_of_their_own_is_not_matched() -> None:
    assert pairs("Toy Symphony, K. 1", "Mozart, Leopold") == []
    assert pairs("Cantata, K. 1", "Scarlatti, Alessandro") == []
    assert composer_catalogues("Franz Xaver Wolfgang Mozart") is None


@pytest.mark.parametrize("spelling", ["Hob. XVI:52", "Hob.XVI/52", "Hob. xvi.52", "HOB 16:52", "Hob. XVI 52"])
def test_hoboken_separators_are_unified(spelling: str) -> None:
    assert pairs(f"Piano Sonata in E-flat, {spelling}") == [("Hob", "XVI:52")]


def test_a_hoboken_group_keeps_its_letter() -> None:
    assert pairs("Mass, Hob. XXIIa:4") == [("Hob", "XXIIa:4")]


@pytest.mark.parametrize(
    ("title", "composer", "expected"),
    [
        ("Sonata in B-flat, D 960", "Schubert, Franz", [("D", "960")]),
        ("Sonata in B-flat, D 960", "Beethoven, Ludwig van", []),
        ("Sonata in B-flat, D 960", None, []),
        ("Symphony No. 9, B. 178", "Dvořák, Antonín", [("B", "178")]),
        ("Symphony No. 9, B. 178", "Brahms, Johannes", []),
        ("Clair de lune, L. 75", "Claude Debussy", [("L", "75")]),
        ("The Planets, Op. 32, H. 125", "Holst, Gustav", [("H", "125")]),
    ],
)
def test_bare_letters_count_only_for_the_composer_who_uses_them(
    title: str, composer: str | None, expected: list[tuple[str, str]]
) -> None:
    assert pairs(title, composer) == expected


def test_liszts_searle_and_werkverzeichnis_numbers_stay_apart() -> None:
    assert pairs("Sonata in B minor, S. 178, LW A179", "Liszt, Franz") == [("S", "178"), ("LW", "a179")]


def test_universal_catalogues_need_no_composer() -> None:
    title = "BWV 1048, HWV 56, TWV 55:e1, BuxWV 76, SWV 22, RV 269"
    assert pairs(title) == [
        ("BWV", "1048"),
        ("HWV", "56"),
        ("TWV", "55:e1"),
        ("BuxWV", "76"),
        ("SWV", "22"),
        ("RV", "269"),
    ]


def test_appendices_are_their_own_schemes() -> None:
    assert pairs("Minuet, BWV Anh. 114") == [("BWV Anh", "114")]
    assert pairs("Kyrie, KV Anh. 15", MOZART) == [("KV Anh", "15")]
    assert pairs("Kyrie, K. Anh. 15", MOZART) == [("KV Anh", "15")]


def test_woo_and_anh_are_read_for_any_composer() -> None:
    assert pairs("Für Elise, WoO 59", "Beethoven, Ludwig van") == [("WoO", "59")]
    assert pairs("Hungarian Dances, WoO 1", "Brahms, Johannes") == [("WoO", "1")]


def test_a_key_or_a_word_is_never_a_catalogue_number() -> None:
    assert pairs("Toccata and Fugue in D minor", "Schubert, Franz") == []
    assert pairs("Rhapsodies, Vol. 2", "Liszt, Franz") == []


def test_normalize_catalogue_refuses_what_is_not_a_catalogue() -> None:
    assert normalize_catalogue("XYZ", "12") == ()
    assert normalize_catalogue("BWV", "abc") == ()
    assert normalize_catalogue("K", "550", MOZART) == (("KV", "550"),)


def test_parse_catalogue_reads_a_structured_field_with_its_opus() -> None:
    assert parse_catalogue("Op.27 No.2 ; K.331", MOZART) == {("KV", "331"), ("op", "27/2")}
    assert parse_catalogue("Op.67") == {("op", "67")}
    assert parse_catalogue("ILB 193") == frozenset()
