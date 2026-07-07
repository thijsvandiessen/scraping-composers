"""Tests for extracting structured features from raw work titles."""

from composer_warehouse.works.extract import extract_features


def test_symphony_with_number_key_and_opus() -> None:
    f = extract_features("Symphony No. 5 in C minor, Op. 67")
    assert f.work_type == "symphony"
    assert f.number == 5
    assert f.musical_key == "c minor"
    assert f.opus_number == "67"
    assert f.catalogue_prefix is None


def test_bach_catalogue_number() -> None:
    f = extract_features("Prelude and Fugue in C major, BWV 846")
    assert f.catalogue_prefix == "BWV"
    assert f.catalogue_number == "846"
    assert f.musical_key == "c major"


def test_mozart_kochel_number() -> None:
    f = extract_features("Piano Sonata No. 11 in A major, K. 331")
    assert f.catalogue_prefix == "K"
    assert f.catalogue_number == "331"
    assert f.work_type == "sonata"
    assert f.number == 11


def test_german_title_number_and_key() -> None:
    f = extract_features("Sinfonie Nr. 5 c-moll")
    assert f.work_type == "symphony"
    assert f.number == 5
    assert f.musical_key == "c minor"


def test_opus_with_sub_number() -> None:
    f = extract_features("String Quartet in B-flat major, Op. 18 No. 1")
    assert f.work_type == "quartet"
    assert f.opus_number == "18"
    assert f.number == 1
    assert f.musical_key == "b flat major"


def test_in_d_minor_is_not_read_as_a_catalogue_number() -> None:
    # "d" is a catalogue prefix (Schubert) but only with a following number
    f = extract_features("Toccata and Fugue in D minor")
    assert f.catalogue_prefix is None
    assert f.musical_key == "d minor"
    assert f.work_type == "fugue"


def test_core_title_strips_structured_spans_but_keeps_nickname() -> None:
    f = extract_features('Symphony No. 3 in E-flat major, Op. 55 "Eroica"')
    assert "eroica" in f.core_title
    assert "67" not in f.core_title
    # the number/key/opus live in their own fields, not the residual
    assert f.number == 3
    assert f.opus_number == "55"


def test_plain_title_has_no_features() -> None:
    f = extract_features("Clair de lune")
    assert f.work_type is None
    assert f.opus_number is None
    assert f.catalogue_prefix is None
    assert f.musical_key is None
    assert f.number is None
    assert f.normalized_title == "clair de lune"
