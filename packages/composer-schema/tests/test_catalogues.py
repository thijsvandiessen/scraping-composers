"""Catalogue numbers normalised per composer (#351)."""

import pytest
from composer_schema.catalogues import (
    CATALOGUE_SCHEMES,
    COMPOSERS,
    MAX_RANGE,
    UNIVERSAL,
    WIKIDATA_CATALOGUES,
    WIKIDATA_OPUS,
    catalogue_members,
    composer_catalogues,
    find_catalogues,
    is_catalogue_set,
    normalize_catalogue,
    normalize_wikidata_code,
    parse_catalogue,
)

MOZART = "Mozart, Wolfgang Amadeus"
SCARLATTI = "Scarlatti, Domenico"
BACH = "Bach, Johann Sebastian"
TELEMANN = "Telemann, Georg Philipp"


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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("6 Brandenburg Concertos, BWV 1046-1051", ("BWV", "1046-1051")),
        ("6 Brandenburg Concertos, BWV 1046\u20131051", ("BWV", "1046-1051")),
        ("6 Brandenburg Concertos, BWV 1046 \u2014 1051", ("BWV", "1046-1051")),
        ("6 Brandenburg Concertos, BWV 1046 to 1051", ("BWV", "1046-1051")),
        ("12 Concerti Grossi, Op.6, HWV 319-330", ("HWV", "319-330")),
        ("12 Fantasias, TWV 40:2-13", ("TWV", "40:2-13")),
        ("Twelve Fantasias for Viola da Gamba TWV 40: 26\u201337", ("TWV", "40:26-37")),
        ("Six Sonatas, Hob. XVI:1-6", ("Hob", "XVI:1-6")),
    ],
)
def test_a_range_is_one_reference_to_the_whole_set(title: str, expected: tuple[str, str]) -> None:
    assert pairs(title, BACH) == [expected]


@pytest.mark.parametrize(
    ("field", "composer", "first"),
    [
        ("BWV 1046-1051", BACH, ("BWV", "1046")),
        ("BWV 1046\u20131051", BACH, ("BWV", "1046")),
        ("TWV 40:2-13", TELEMANN, ("TWV", "40:2")),
        ("HWV 319-330", None, ("HWV", "319")),
        ("Z.802-811", "Purcell, Henry", ("Z", "802")),
        ("BWV 1046, 1047", BACH, ("BWV", "1046")),
    ],
)
def test_a_range_or_list_never_stands_for_its_first_piece(
    field: str, composer: str | None, first: tuple[str, str]
) -> None:
    assert first not in parse_catalogue(field, composer)


def test_ranges_read_alike_across_schemes() -> None:
    assert parse_catalogue("BWV 1046-1051", BACH) == {("BWV", "1046-1051")}
    assert parse_catalogue("TWV 40:2-13", TELEMANN) == {("TWV", "40:2-13")}
    assert parse_catalogue("TWV 40:2", TELEMANN) == {("TWV", "40:2")}


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Brandenburg Concertos, BWV 1046, 1047", ("BWV", "1046-1047")),
        ("12 Minuets, K.599, 601, 604", ("KV", "599,601,604")),
        ("Coronation Anthems HWV 259, 258, 260 und 261", ("HWV", "258-261")),
        ("Concerto for Oboe (from BWV 105 & 170)", ("BWV", "105,170")),
        (
            "Fantasies, Preludes, Fugues. BWV 917, 918, 921, 894-896, 903a",
            ("BWV", "894-896,903a,917-918,921"),
        ),
    ],
)
def test_a_list_is_one_canonical_set(title: str, expected: tuple[str, str]) -> None:
    assert pairs(title, MOZART) == [expected]


def test_a_list_and_a_range_of_the_same_works_agree() -> None:
    assert parse_catalogue("HWV 258-261") == parse_catalogue("HWV 259, 258, 260 und 261")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Partita 1, Sarabande, BWV 825-4", ("BWV", "825")),  # a movement number
        ("Sonata in B-flat, D 960 - 1828", ("D", "960")),  # a year
        ("Lobe den Herren BWV 57, 8", ("BWV", "57")),  # a cantata's movement
        ("Aria, BWV 988,1", ("BWV", "988")),
        ("Cantata BWV 140, 1731", ("BWV", "140")),
        ("Brandenburg Concerto No. 5, BWV 1050, 1st movement", ("BWV", "1050")),
    ],
)
def test_what_only_looks_like_a_range_or_list_keeps_its_first_number(
    title: str, expected: tuple[str, str]
) -> None:
    assert pairs(title, "Schubert, Franz") == [expected]


def test_a_tail_that_is_no_set_stays_in_the_text() -> None:
    (ref,) = find_catalogues("Lobe den Herren BWV 57, 8")
    assert (ref.number, ref.span) == ("57", (16, 22))


def test_separate_ranges_stay_separate_references() -> None:
    assert pairs("11 Praeludien BWV 846-851, BWV 853-857") == [("BWV", "846-851"), ("BWV", "853-857")]


def test_an_overlong_set_is_dropped_rather_than_cut() -> None:
    field = "BWV " + ", ".join(str(n) for n in range(1000, 1040, 2))
    assert parse_catalogue(field) == frozenset()


def test_catalogue_members_expand_a_set() -> None:
    assert catalogue_members("40:2-4") == {"40:2", "40:3", "40:4"}
    assert catalogue_members("894-896,903a") == {"894", "895", "896", "903a"}
    assert catalogue_members("1046") == {"1046"}
    assert len(catalogue_members(f"1-{1 + MAX_RANGE}")) == MAX_RANGE + 1
    assert catalogue_members(f"1-{2 + MAX_RANGE}") == {f"1-{2 + MAX_RANGE}"}
    assert catalogue_members("825-4") == {"825-4"}
    mbid = "4dad1f72-5f8d-3a63-ae31-799131026480"
    assert catalogue_members(mbid) == {mbid}
    assert is_catalogue_set("1046-1051") and is_catalogue_set("818,819a")
    assert not is_catalogue_set("XVI:52") and not is_catalogue_set("173db")


CPE_BACH = "Bach, Carl Philipp Emanuel"
WF_BACH = "Bach, Wilhelm Friedemann"
JC_BACH = "Johann Christian Bach"
CHOPIN = "Chopin, Frédéric"


@pytest.mark.parametrize(
    ("title", "composer", "expected"),
    [
        ("Flute Sonata in D major, H.561, Wq.131", CPE_BACH, [("H", "561"), ("Wq", "131")]),
        ("Polonaise in E-flat minor, F.12", WF_BACH, [("F", "12")]),
        ("Fantasia, Fk 16", "Wilhelm Friedemann Bach", [("F", "16")]),
        ("Symphony in D, Warb C 9", JC_BACH, [("Warb", "c9")]),
        ("Trio Sonata in C major, Z.795", "Purcell, Henry", [("Z", "795")]),
        ("Missa, LV 1144", "Lassus, Orlande de", [("LV", "1144")]),
        ("Ad coenam agni providi, LV Anh.1", "Orlando di Lasso", [("LV Anh", "1")]),
        ("Pange lingua, MH 395", "Haydn, Michael", [("MH", "395")]),
        ("Waltz in E minor, B.56", CHOPIN, [("B", "56")]),
        ("Nocturne in C-sharp minor, KK IVa/16", CHOPIN, [("KK", "IVa/16")]),
        ("Mazurka, KK Anh. Ia/1", CHOPIN, [("KK Anh", "Ia/1")]),
        ("Der Rosenkavalier, suite, TrV 227d, AV 145", "Strauss, Richard", [("TrV", "227d"), ("AV", "145")]),
        ("Metamorphosen, AV 142", "Richard Strauß", [("AV", "142")]),
        ("Lohengrin, WWV 75", "Wagner, Richard", [("WWV", "75")]),
        ("Piano Concerto No.2, BB 101, Sz. 95", "Béla Bartók", [("BB", "101"), ("Sz", "95")]),
        ("Symphony No. 8 in C minor, WAB 108", "Anton Bruckner", [("WAB", "108")]),
        ("Længsel, CNW 356", "Nielsen, Carl", [("CNW", "356")]),
        ("Missa votiva, ZWV 18", "Zelenka, Jan Dismas", [("ZWV", "18")]),
        ("Overture, GWV 413", "Graupner, Christoph", [("GWV", "413")]),
        ("Cello Sonata, FP 143", "Poulenc, Francis", [("FP", "143")]),
        ("In the Mists, JW VIII/22", "Leoš Janáček", [("JW", "VIII/22")]),
        ("Hudba ke kroužení kužely, JW 8/13", "Janáček, Leoš", [("JW", "VIII/13")]),
    ],
)
def test_composer_scoped_catalogues(title: str, composer: str, expected: list[tuple[str, str]]) -> None:
    assert pairs(title, composer) == expected


@pytest.mark.parametrize(
    ("title", "composer"),
    [
        ("Prelude, H. 88", BACH),  # Helm is C.P.E.'s, not his father's
        ("Toccata, F 7.32", "Frescobaldi, Girolamo"),  # Falck is W.F. Bach's
        ("Sonata, Wq.54", "Gluck, Christoph Willibald"),  # Wotquenne catalogued Gluck too
        ("Trio, Z 12", "Purcell, Daniel"),
        ("Cantata, LV 16", "Zachow, Friedrich Wilhelm"),
        ("Symphony, MH 12", "Haydn, Joseph"),
        ("Waltz, B. 13", "Pleyel, Ignaz"),
        ("Polka, TrV 12", "Strauss, Johann"),
        ("Song, CNW 12", "Nielsen, Ludolf"),
    ],
)
def test_composer_scoped_letters_mean_nothing_for_anyone_else(title: str, composer: str) -> None:
    assert pairs(title, composer) == []


def test_kk_is_kobylanska_for_chopin_and_kirkpatrick_for_scarlatti() -> None:
    assert pairs("Sonata, KK 12", SCARLATTI) == [("Kk", "12")]
    assert pairs("Polonaise, KK IVa/1", CHOPIN) == [("KK", "IVa/1")]


def test_the_bach_sons_are_told_apart_by_given_name() -> None:
    assert composer_catalogues(CPE_BACH) != composer_catalogues(WF_BACH)
    assert composer_catalogues(JC_BACH) is not None
    assert composer_catalogues(BACH) is None
    assert composer_catalogues("Bach, Johann Christoph Friedrich") is None


def test_purcells_z_range_is_one_set() -> None:
    assert parse_catalogue("Z.802-811", "Purcell, Henry") == {("Z", "802-811")}
    assert pairs("Fantazias, Z.732-747, 752", "Henry Purcell") == [("Z", "732-747,752")]


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("Wq 117:39", ("Wq", "117/39")),
        ("Wq.118/6", ("Wq", "118/6")),
        ("Wq.89.3", ("Wq", "89/3")),
        ("Wq 202:C1", ("Wq", "202/c1")),
        ("Wq 197:1-30", ("Wq", "197/1-30")),
        ("H 542.5", ("H", "542.5")),
        ("H.780.1-2", ("H", "780.1-2")),
        ("BR-CPEB C 48.1", ("BR-CPEB", "c48.1")),
        ("BR‑CPEB Dp 4", ("BR-CPEB", "dp4")),
    ],
)
def test_cpe_bach_numbers_within_a_collection(field: str, expected: tuple[str, str]) -> None:
    assert parse_catalogue(field, CPE_BACH) == {expected}


def test_a_dotted_number_elsewhere_is_its_work() -> None:
    assert pairs("Prelude, BWV 846.2") == [("BWV", "846")]


def test_a_letter_that_is_no_reference_does_not_hide_the_one_after_it() -> None:
    assert pairs("5. Quartett in F KV 158", MOZART) == [("KV", "158")]
    assert pairs("Messe in F D 105", "Schubert, Franz") == [("D", "105")]
    assert pairs("Presto, H/K WoO 5,2", "Schumann, Robert") == [("WoO", "5")]


def test_a_range_with_a_non_breaking_hyphen_is_a_range() -> None:
    assert parse_catalogue("BWV 1046‑1051", BACH) == {("BWV", "1046-1051")}


@pytest.mark.parametrize(
    ("code", "item", "short", "composer", "expected"),
    [
        # a catalogue item with no short name, for any composer
        ("238/3", "Q139766427", None, JC_BACH, (("Ty", "238/3"),)),
        ("345/1", "Q139766427", None, "Wilhelm Friedrich Ernst Bach", (("Ty", "345/1"),)),
        ("545", "Q138770768", None, BACH, (("H", "545"),)),
        # the Köchel editions: plain numbers are KV, lettered ones KV6
        ("87", "Q22338962", None, MOZART, (("KV", "87"),)),
        ("74a", "Q66069477", None, MOZART, (("KV6", "74a"),)),
        ("173dB", "Q22340538", None, MOZART, (("KV6", "173db"),)),
        ("K. 184/161a", "Q162478", "K.", MOZART, (("KV", "184"), ("KV6", "161a"))),
        # the item decides over a letter the code shares with another catalogue
        ("H 35", "Q139309403", "BR-CPEB", "Carl Philipp Emanuel Bach", (("BR-CPEB", "h35"),)),
        ("H 88", "Q138770768", None, "Carl Philipp Emanuel Bach", (("H", "88"),)),
        ("C 48.1", "Q139309403", "BR-CPEB", "Carl Philipp Emanuel Bach", (("BR-CPEB", "c48.1"),)),
        ("XC 3‑5", "Q139743120", "Warb", MOZART, (("Warb", "xc3-5"),)),
        ("KK IIb/3", "Q16747642", "KK", CHOPIN, (("KK", "IIb/3"),)),
        ("VI/18", "Q136745429", None, "Leoš Janáček", (("JW", "VI/18"),)),
        ("626", "Q203664", None, "Henry Purcell", (("Z", "626"),)),
        # a code stated in another catalogue than its item is read as written
        ("TrV 209", "Q26160336", "AV", "Richard Strauss", (("TrV", "209"),)),
        # no item: read as a title would be
        ("BB 101", None, None, "Béla Bartók", (("BB", "101"),)),
        ("550", None, "KV", MOZART, (("KV", "550"),)),
    ],
)
def test_wikidata_codes_are_read_by_their_catalogue_item(
    code: str, item: str | None, short: str | None, composer: str, expected: tuple[tuple[str, str], ...]
) -> None:
    assert normalize_wikidata_code(code, item, short, composer) == expected


@pytest.mark.parametrize(
    ("code", "item", "expected"),
    [
        ("C 48", "Q16749680", (("wd:Q16749680", "c 48"),)),  # Chomiński
        ("P 86", "Q71108028", (("wd:Q71108028", "p 86"),)),  # Pestelli
        ("54-59", "Q1", (("wd:Q1", "54-59"),)),  # a range stays a set
        ("960602-001", "Q1", (("wd:Q1", "960602 001"),)),  # but this is no range
        ("R. op. 243", "Q112328324", (("wd:Q112328324", "r. op. 243"),)),  # Rinaldi, not an opus
        ("op. 239", "Q112328324", (("wd:Q112328324", "op. 239"),)),  # nor without his initial
        ("P. deest", "Q112328398", ()),  # not in the catalogue: nothing to match on
        ("Op. 32", WIKIDATA_OPUS, (("op", "32"),)),
        ("deest", None, ()),
    ],
)
def test_a_wikidata_code_in_an_unknown_catalogue_keeps_the_item_as_its_scheme(
    code: str, item: str | None, expected: tuple[tuple[str, str], ...]
) -> None:
    assert normalize_wikidata_code(code, item, None, "Antonio Vivaldi") == expected


def test_the_wikidata_map_names_only_schemes_the_parser_knows() -> None:
    known = {scheme.code for scheme in UNIVERSAL} | {
        scheme.code for entry in COMPOSERS for scheme in entry.schemes
    }
    assert CATALOGUE_SCHEMES == known | {"KV6", "KV6 Anh"}
    assert {scheme.code for scheme in WIKIDATA_CATALOGUES.values()} <= known


def test_the_wikidata_map_reuses_the_parsers_schemes() -> None:
    # the very objects, so a prefix or rule added to one is the other's too
    parser = [*UNIVERSAL, *(scheme for entry in COMPOSERS for scheme in entry.schemes)]
    for qid, scheme in WIKIDATA_CATALOGUES.items():
        assert any(scheme is other for other in parser), qid


@pytest.mark.parametrize(
    ("code", "item", "short", "composer", "expected"),
    [
        # a code in a sibling or universal catalogue is a number there
        ("Wq 117", "Q139309403", "BR-CPEB", CPE_BACH, (("Wq", "117"),)),
        ("Ty 238", "Q139743120", "Warb", JC_BACH, (("Ty", "238"),)),
        ("Ty 238", "Q139743120", "Warb", "Gioacchino Cocchi", (("Ty", "238"),)),
        ("BWVAnh. 12", "Q739093", None, BACH, (("BWV Anh", "12"),)),
        # but a section that only looks like a prefix stays a section
        ("Fp 12", "Q139309403", "BR-CPEB", CPE_BACH, (("BR-CPEB", "fp12"),)),
    ],
)
def test_a_wikidata_code_stated_in_another_catalogue_is_read_in_that_one(
    code: str, item: str, short: str, composer: str, expected: tuple[tuple[str, str], ...]
) -> None:
    assert normalize_wikidata_code(code, item, short, composer) == expected


@pytest.mark.parametrize("code", ["KV6 173dB", "KV⁶ 173dB", "K6 173dB", "K.6 173dB"])
def test_a_koechel_edition_marker_is_no_number(code: str) -> None:
    assert normalize_wikidata_code(code, "Q22340538", None, MOZART) == (("KV6", "173db"),)


def test_a_prefix_run_into_its_number_is_still_stripped() -> None:
    assert normalize_wikidata_code("KV65", "Q162478", None, MOZART) == (("KV", "65"),)
    assert normalize_wikidata_code("K6", "Q162478", None, MOZART) == (("KV", "6"),)


@pytest.mark.parametrize(
    ("code", "item", "expected"),
    [
        ("Anh. 229", "Q162478", (("KV Anh", "229"),)),
        ("K Anh. 12", "Q162478", (("KV Anh", "12"),)),
        ("Anh. 159", "Q214203", (("BWV Anh", "159"),)),
        ("Anh 159", "Q214203", (("BWV Anh", "159"),)),
        # the sixth edition's lettered appendix numbers anew, as KV6 does
        ("Anh. C 27.06", "Q22340538", (("KV6 Anh", "c27.06"),)),
        ("Anh. C.27.09", "Q162478", (("KV6 Anh", "c27.09"),)),
        ("KK Anh.II/1", "Q16747642", (("KK Anh", "II/1"),)),
    ],
)
def test_a_bare_appendix_in_a_catalogue_item_is_that_catalogues_appendix(
    code: str, item: str, expected: tuple[tuple[str, str], ...]
) -> None:
    assert normalize_wikidata_code(code, item, None, MOZART) == expected
    assert {scheme for scheme, _ in expected} <= CATALOGUE_SCHEMES


def test_the_koechel_appendix_reads_alike_in_a_title() -> None:
    assert pairs("Fugue, KV Anh. C 27.06", MOZART) == [("KV6 Anh", "c27.06")]
    assert parse_catalogue("K.Anh.226 (196e; K.Anh.C.17.01)", MOZART) == {
        ("KV Anh", "226"),
        ("KV6 Anh", "c17.01"),
    }
    assert pairs("Fugue, K. Anh. 229", MOZART) == [("KV Anh", "229")]


def test_a_code_written_as_an_opus_keeps_its_opus_in_any_catalogue() -> None:
    assert normalize_wikidata_code("Op. 64 No. 1", "Q16749676", "B", CHOPIN) == (("op", "64/1"),)
    assert normalize_wikidata_code("Opus 28", "Q16749676", "B", CHOPIN) == (("op", "28"),)


@pytest.mark.parametrize(
    ("code", "item"),
    [
        ("9978452411", "Q51796663"),  # SACEM
        ("71200675600", "Q64029229"),  # SIAE
        ("960602-001", "Q54959446"),  # GEMA
        ("HCD-2018", "Q107212725"),  # a record label's release, holding many works
        ("22072077", "Q104828762"),  # a library's holding
    ],
)
def test_a_code_in_no_composers_work_catalogue_is_no_work_number(code: str, item: str) -> None:
    assert normalize_wikidata_code(code, item, None, "Thelonious Monk") == ()


def test_hoboken_drops_the_part_of_a_work() -> None:
    assert parse_catalogue("Hob. I 104.2", "Haydn") == {("Hob", "I:104")}
    assert parse_catalogue("Hob. XVI:52.3", "Haydn") == {("Hob", "XVI:52")}
    assert parse_catalogue("Hob 16.52", "Haydn") == {("Hob", "XVI:52")}


@pytest.mark.parametrize("composer", ["Martinu", "Holst, Gustav", "Arthur Honegger"])
def test_only_helm_numbers_have_a_dotted_part_of_their_own(composer: str) -> None:
    assert parse_catalogue("Quartet, H. 103.2", composer) == {("H", "103")}
    assert parse_catalogue("Quartet, H. 103.2", CPE_BACH) == {("H", "103.2")}
    assert normalize_wikidata_code("103.2", "Q141568820", None, composer) == (("H", "103"),)
    assert normalize_wikidata_code("542.5", "Q138770768", None, composer) == (("H", "542.5"),)


@pytest.mark.parametrize(
    ("composer", "expected"),
    [
        ("C.P.E. Bach", "Wq"),
        ("Bach, C. P. E.", "Wq"),
        ("C.Ph.E. Bach", "Wq"),
        ("Bach, J.C.", "Warb"),
        ("W.F. Bach", "F"),
        ("Strauss, R.", "TrV"),
        ("R. Wagner", "WWV"),
        ("M. Haydn", "MH"),
        # initials count only in full, and only the right ones
        ("J.S. Bach", None),
        ("J.C.F. Bach", None),
        ("J. Strauss", None),
        ("Strauss, J.", None),
        ("S. Wagner", None),
    ],
)
def test_initials_stand_for_the_given_names(composer: str, expected: str | None) -> None:
    entry = composer_catalogues(composer)
    assert (entry.schemes[0].code if entry else None) == expected


def test_initials_unlock_the_composers_catalogues() -> None:
    assert pairs("Sonata, Wq. 49/1", "C.P.E. Bach") == [("Wq", "49/1")]
    assert pairs("Polka, TrV 12", "J. Strauss") == []
