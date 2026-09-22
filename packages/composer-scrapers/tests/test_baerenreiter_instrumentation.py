"""Tests for reading Bärenreiter's "Instrumentation in detail" list."""

from __future__ import annotations

import pytest
from composer_scrapers.baerenreiter.instrumentation import (
    parse_detail,
    parse_part,
    scoring_categories,
    split_parts,
    string_instruments,
    string_section,
)

# Verbatim from a Bärenreiter orchestral product page.
ORCHESTRA = (
    "Piccolo flute (Flute), Flute (2) (Piccolo flute), Oboe (2), Cor anglais, Clarinet (2), "
    "Bass clarinet, Bassoon (2), Contrabassoon, Horn (4), Trumpet (3), Trombone (3), Tuba, "
    "Timpani, Triangle, Concert bass drum, Cymbals, Harp, Violin (2), Viola, Violoncello, Double bass"
)


def test_an_orchestral_list_reads_part_by_part_with_counts_and_doublings() -> None:
    parts = {part.stated: part for part in parse_detail(ORCHESTRA).parts}

    assert len(parts) == 21
    assert parts["Piccolo flute (Flute)"].instrument == "piccolo"
    assert parts["Piccolo flute (Flute)"].also == ("flute",)
    flutes = parts["Flute (2) (Piccolo flute)"]
    assert (flutes.instrument, flutes.count, flutes.also) == ("flute", 2, ("piccolo",))
    assert (parts["Horn (4)"].instrument, parts["Horn (4)"].count) == ("horn", 4)
    assert parts["Cor anglais"].instrument == "english horn"
    assert parts["Concert bass drum"].instrument == "bass drum"
    assert parts["Violoncello"].instrument == "cello"
    assert parts["Violin (2)"].count == 2


def test_the_list_names_every_instrument_once_in_score_order() -> None:
    detail = parse_detail(ORCHESTRA)

    assert detail.unmatched == ()
    assert detail.instruments[:3] == ("piccolo", "flute", "oboe")
    assert detail.instruments[-4:] == ("violin", "viola", "cello", "double bass")
    assert len(detail.instruments) == len(set(detail.instruments))


def test_commas_inside_a_parenthetical_do_not_split_the_list() -> None:
    assert split_parts("Basso continuo (Violoncello, Organ), Violin (2)") == [
        "Basso continuo (Violoncello, Organ)",
        "Violin (2)",
    ]


def test_a_continuo_names_the_instruments_that_realise_it() -> None:
    part = parse_part("Basso continuo (Violoncello, Double bass, Harpsichord)")

    assert part.instrument == "basso continuo"
    assert part.also == ("cello", "double bass", "harpsichord")


@pytest.mark.parametrize(
    ("stated", "instrument", "count", "voicing"),
    [
        ("Mixed choir (SATB)", "mixed choir", None, "SATB"),
        ("Mixed choir (SATB) (2)", "mixed choir", 2, "SATB"),
        ("Mixed choir (2): SSAB", "mixed choir", 2, "SSAB"),
        ("Frauenchor: SSA", "female choir", None, "SSA"),
        ("FCh-SMezA", "female choir", None, "SMezA"),
        ("GemCh-SATB", "mixed choir", None, "SATB"),
        ("TTBB", "choir", None, "TTBB"),
    ],
)
def test_a_choir_keeps_its_voicing(stated: str, instrument: str, count: int | None, voicing: str) -> None:
    part = parse_part(stated)

    assert (part.instrument, part.count, part.voicing) == (instrument, count, voicing)


@pytest.mark.parametrize(
    ("stated", "instrument"),
    [
        ("1. Violine", "violin"),
        ("2. Violin", "violin"),
        ("Horn in F", "horn"),
        ("Sopran-Blockflöte", "descant recorder"),
        ("Oboe d´amore", "oboe d'amore"),
        ("Piano (4 hands)", "piano four hands"),
        ("Klavier 4-händig", "piano four hands"),
        ("Gemischter Chor (SATB) oder Gemischter Chor", "mixed choir"),
    ],
)
def test_the_name_is_read_past_ordinals_keys_and_spelling(stated: str, instrument: str) -> None:
    assert parse_part(stated).instrument == instrument


def test_solo_and_ad_libitum_are_flags_not_other_instruments() -> None:
    solo = parse_part("Violin solo (2)")
    ad_lib = parse_part("Trombone (3) ad libitum")

    assert (solo.instrument, solo.count, solo.solo) == ("violin", 2, True)
    assert not parse_part("Violin").solo
    assert (ad_lib.instrument, ad_lib.count, ad_lib.ad_libitum) == ("trombone", 3, True)


def test_a_solo_voice_written_as_its_letter_is_read_only_with_solo() -> None:
    assert parse_part("S-solo").instrument == "soprano"
    assert parse_part("Mez-solo").instrument == "mezzo-soprano"
    assert parse_part("S").instrument is None


def test_an_unknown_name_or_real_alternative_is_left_unresolved_not_guessed() -> None:
    detail = parse_detail("Instrument ad libitum, Organ or Piano, Violin")

    assert [part.instrument for part in detail.parts] == [None, None, "violin"]
    assert detail.unmatched == ("Instrument ad libitum", "Organ or Piano")


def test_an_unread_parenthetical_is_kept_as_a_qualifier() -> None:
    part = parse_part("Trumpet (ripieno)")

    assert (part.instrument, part.qualifiers) == ("trumpet", ("ripieno",))


def test_the_scoring_field_is_read_entry_by_entry_as_what_the_work_is_for() -> None:
    assert scoring_categories("Soprano solo, Strings") == ("soprano", "strings")
    assert scoring_categories("Violin, Orchestra") == ("violin", "orchestra")
    assert scoring_categories("Piano (4 hands)") == ("piano four hands", "piano")
    assert scoring_categories(None) == ()


@pytest.mark.parametrize(
    "stated",
    ["str (4.4.3.2.1)", "Str (4,4,3,2,1)", "Str. (mindestens 4, 4, 3, 2, 1)"],
)
def test_a_sized_string_section_is_read_part_by_part(stated: str) -> None:
    section = string_section(stated)

    assert section is not None
    assert section == {"violin I": 4, "violin II": 4, "viola": 3, "cello": 2, "double bass": 1}
    assert string_instruments(section) == ("violin", "viola", "cello", "double bass")


def test_a_zero_sized_string_part_is_absent() -> None:
    section = string_section("Str (8,0,3,2,1)")

    assert section is not None
    assert string_instruments(section) == ("violin", "viola", "cello", "double bass")
    assert string_instruments({"violin I": 0, "violin II": 0, "viola": 2, "cello": 0, "double bass": 0}) == (
        "viola",
    )
    assert string_section("Str") is None
