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
    f = extract_features("Piano Sonata No. 11 in A major, K. 331", composer="Mozart, Wolfgang Amadeus")
    assert f.catalogue_prefix == "KV"
    assert f.catalogue_number == "331"
    assert f.identifiers == {("KV", "331")}
    assert f.work_type == "sonata"
    assert f.number == 11


def test_a_bare_k_means_nothing_without_the_composer() -> None:
    # K is Köchel for Mozart, Kirkpatrick for Scarlatti, and nothing for anyone else
    assert extract_features("Piano Sonata No. 11 in A major, K. 331").identifiers == frozenset()
    scarlatti = extract_features("Sonata in D minor, K. 9", composer="Scarlatti, Domenico")
    assert scarlatti.identifiers == {("Kk", "9")}


def test_every_catalogue_number_in_the_title_is_an_identifier() -> None:
    f = extract_features("Sonata in D minor, K. 9, L. 413", composer="Scarlatti, Domenico")
    assert f.identifiers == {("Kk", "9"), ("L", "413")}
    assert (f.catalogue_prefix, f.catalogue_number) == ("Kk", "9")  # the first, for display


def test_structured_fields_add_catalogue_numbers_and_an_opus() -> None:
    f = extract_features("Piano Sonata No.14", composer="Beethoven, Ludwig van", structured=["Op.27 No.2"])
    assert (f.opus_number, f.number) == ("27", 14)
    f = extract_features("Symphony in G minor", composer="Mozart, Wolfgang Amadeus", structured=["K.550"])
    assert f.identifiers == {("KV", "550")}


def test_stated_pairs_join_the_titles_own_numbers() -> None:
    f = extract_features("Quintet, Op. 11 No. 6", stated=[("Ty", "238/3"), ("op", "99")])
    assert f.identifiers == {("Ty", "238/3")}
    assert f.opus_number == "11"  # the title's opus wins, as with a structured field


def test_an_imslp_internal_number_is_not_a_catalogue() -> None:
    assert extract_features("Sonata", structured=["ILB 193"]).identifiers == frozenset()


def test_a_movement_carries_its_parents_number_as_the_parents() -> None:
    f = extract_features("Allegro (From Brandenburg Concerto No. 3, BWV 1048)")
    assert f.derivation == "part"
    assert f.identifiers == frozenset()
    assert f.parent_identifiers == {("BWV", "1048")}
    assert (f.catalogue_prefix, f.catalogue_number) == ("BWV", "1048")


def test_arrangement_and_version_cues() -> None:
    arranged = extract_features("Brandenburg Concerto No. 3, BWV 1048 (arr. for woodwind quintet)")
    assert arranged.derivation == "arrangement"
    assert extract_features("Konzert (Bearb. für Klavier)").derivation == "arrangement"
    assert extract_features("Sinfonie in g KV 550 (1. Fassung)").derivation == "version"
    assert extract_features("Symphony No. 4 (1841 version)").derivation == "version"
    assert extract_features("Sonata (Urfassung)").derivation == "version"


def test_words_that_merely_contain_a_cue_are_not_derived() -> None:
    assert extract_features("Arrival of the Queen of Sheba").derivation is None
    assert extract_features("Concerto in A major, from the Paris set").derivation is None


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


def test_a_range_is_one_identifier_not_its_first_number() -> None:
    f = extract_features("6 Brandenburg Concertos, BWV 1046-1051")
    assert f.identifiers == {("BWV", "1046-1051")}
    assert (f.catalogue_prefix, f.catalogue_number) == ("BWV", "1046-1051")
    assert "1051" not in f.core_title


def test_a_set_holding_the_titles_own_number_is_dropped() -> None:
    f = extract_features("Suite 6 E-dur, BWV 817 Franzoesische Suiten BWV 812-817")
    assert f.identifiers == {("BWV", "817")}
