"""Reading publisher orchestral shorthand, in both dialects.

Every case here is a real catalogue entry for a work whose scoring is independently
known, so the assertions are what the notation *means*, not what the parser happens
to do. Chester/Novello packs its digits and separates with ``/``; Boosey & Hawkes
dots them and separates with ``-``.
"""

from __future__ import annotations

import pytest
from composer_extract.shorthand import parse_shorthand

# --- the catalogue entries, by work and dialect --------------------------------

MOZART_25 = ("0202 / 4000 / str[7]", "0.2.0.2 - 4.0.0.0 - strings[6]")
BEETHOVEN_5 = ("3223 / 2230 / timp.perc / str[8]", "3.2.2.3 - 2.2.3.0 - timp - strings[6]")
PICTURES = (
    "3(pic)3(ca)33 / 4331 / timp.5perc / 2 hp.cel / str[9]",
    "3(III=picc).3(III=corA).3.3 - 4.3.3.1 - timp.perc(5) - cel - 2harps - strings[6]",
)
MAHLER_1 = (
    "4(2pic)4(ca)4(bcl)3(cbn) / 7431 / 2timp.perc / hp / str[10]",
    "4(III,IV=picc).4(III=corA).4(III=bcl).3(III=dbn) - 7.4.3.1 - timp(2).perc - harp - strings[6]",
)
RITE = (
    "3(1pic)+pic+afl.4(1ca)+ca.3(1bcl)+Dcl(Ebcl)+bcl.4(1cbn)"
    " / 8(2ttuba).4(1btpt)pictpt.3.2 / 2timp.4perc / str[5]",
    "3(III=picc).picc.afl.4(IV=corA).corA.3(III=bcl).Dcl(=Ebcl).bcl.4(IV=dbn).dbn"
    " - 8(VII,VIII=ttuba).4(IV=btpt).picctpt.3.2"
    " - timp(2).perc(4):crot/cyms/tam - t/tgl/guiro/BD/tamb - strings[11]",
)

#: The four standing woodwind and brass desks of each work, which is the part of
#: the notation both dialects genuinely state identically.
_DESKS = ("flute", "oboe", "clarinet", "bassoon", "horn", "trumpet", "trombone", "tuba")


def _desks(shorthand: str) -> dict[str, int]:
    parsed = parse_shorthand(shorthand)
    assert parsed is not None
    return {name: count for name, count in parsed.counts.items() if name in _DESKS}


@pytest.mark.parametrize(
    ("work", "expected"),
    [
        (MOZART_25, {"oboe": 2, "bassoon": 2, "horn": 4}),
        (
            BEETHOVEN_5,
            {"flute": 3, "oboe": 2, "clarinet": 2, "bassoon": 3, "horn": 2, "trumpet": 2, "trombone": 3},
        ),
        (
            PICTURES,
            {
                "flute": 3,
                "oboe": 3,
                "clarinet": 3,
                "bassoon": 3,
                "horn": 4,
                "trumpet": 3,
                "trombone": 3,
                "tuba": 1,
            },
        ),
        (
            MAHLER_1,
            {
                "flute": 4,
                "oboe": 4,
                "clarinet": 4,
                "bassoon": 3,
                "horn": 7,
                "trumpet": 4,
                "trombone": 3,
                "tuba": 1,
            },
        ),
        (
            RITE,
            {
                "flute": 3,
                "oboe": 4,
                "clarinet": 3,
                "bassoon": 4,
                "horn": 8,
                "trumpet": 4,
                "trombone": 3,
                "tuba": 2,
            },
        ),
    ],
    ids=["mozart25", "beethoven5", "pictures", "mahler1", "rite"],
)
def test_both_dialects_read_the_same_desks(work: tuple[str, str], expected: dict[str, int]) -> None:
    """The positional part is one notation written two ways, so the two spellings
    of a work have to agree about it — and agree with the score."""
    chester, boosey = work
    assert _desks(chester) == expected
    assert _desks(boosey) == expected


def test_a_zero_desk_is_an_absence_not_an_instrument() -> None:
    """Mozart 25's "0.2.0.2" says there are no flutes and no clarinets. A count of
    zero is a real statement, and what it states is that the instrument is out."""
    parsed = parse_shorthand(MOZART_25[1])
    assert parsed is not None
    assert "flute" not in parsed.instruments
    assert "clarinet" not in parsed.instruments
    assert parsed.instruments == ("oboe", "bassoon", "horn", "strings")


@pytest.mark.parametrize(
    ("shorthand", "expected"),
    [
        # "3(pic)" and "3(III=picc)": one of the three flutes doubles on piccolo.
        (PICTURES[0], 1),
        (PICTURES[1], 1),
        # "4(2pic)" and "4(III,IV=picc)": two of the four do — a leading count and
        # a run of roman numerals saying which players.
        (MAHLER_1[0], 2),
        (MAHLER_1[1], 2),
    ],
)
def test_a_parenthetical_says_how_many_players_double(shorthand: str, expected: int) -> None:
    parsed = parse_shorthand(shorthand)
    assert parsed is not None
    assert parsed.counts["piccolo"] == expected


def test_doublings_and_extras_are_named_as_instruments() -> None:
    """Mahler 1's woodwind doublings are the interesting part of its scoring, and
    the reason to read the parentheticals at all."""
    parsed = parse_shorthand(MAHLER_1[1])
    assert parsed is not None
    assert {"piccolo", "english horn", "bass clarinet", "contrabassoon"} <= set(parsed.instruments)
    assert parsed.counts["harp"] == 1
    assert parsed.counts["timpani"] == 2


def test_a_counted_player_is_not_read_as_a_desk() -> None:
    """ "5perc" and "2harps" count players; "3.2" are desks. Only the absence of a
    following word separates the two, so both spellings are pinned."""
    parsed = parse_shorthand(PICTURES[0])
    assert parsed is not None
    assert parsed.counts["percussion"] == 5
    assert parsed.counts["harp"] == 2
    assert parsed.counts["celesta"] == 1


def test_the_boosey_percussion_battery_is_read_across_its_own_dashes() -> None:
    """Boosey hangs the battery off a colon and then keeps using " - " inside it,
    so the detail arrives as what looks like two more sections."""
    parsed = parse_shorthand(RITE[1])
    assert parsed is not None
    assert {"crotales", "cymbals", "tam-tam", "triangle", "bass drum", "tambourine"} <= set(
        parsed.instruments
    )
    assert parsed.counts["percussion"] == 4


def test_the_rite_reads_its_whole_wind_and_brass_apparatus() -> None:
    """The hardest entry in either catalogue: packed digits, "+" additions, roman
    numeral doublings and abbreviations that appear nowhere else."""
    parsed = parse_shorthand(RITE[0])
    assert parsed is not None
    assert {
        "piccolo",
        "alto flute",
        "english horn",
        "bass clarinet",
        "d clarinet",
        "e-flat clarinet",
        "contrabassoon",
        "tenor tuba",
        "bass trumpet",
        "piccolo trumpet",
    } <= set(parsed.instruments)


@pytest.mark.parametrize(
    ("shorthand", "parts"),
    [(MOZART_25[0], 7), (MOZART_25[1], 6), (BEETHOVEN_5[0], 8), (RITE[1], 11)],
)
def test_the_bracket_is_the_number_of_string_parts(shorthand: str, parts: int) -> None:
    """Publishers count string parts differently — Chester says 7 where Boosey says
    6 for the same Mozart — so it is recorded, not reconciled."""
    parsed = parse_shorthand(shorthand)
    assert parsed is not None
    assert parsed.string_parts == parts
    # A string section is a body of players, not one of them.
    assert "strings" in parsed.instruments
    assert "strings" not in parsed.counts


def test_instruments_come_back_in_score_order() -> None:
    parsed = parse_shorthand(BEETHOVEN_5[0])
    assert parsed is not None
    assert parsed.instruments == (
        "flute",
        "oboe",
        "clarinet",
        "bassoon",
        "horn",
        "trumpet",
        "trombone",
        "timpani",
        "percussion",
        "strings",
    )


def test_the_two_dialects_can_disagree_about_what_they_state() -> None:
    """Not a parser difference: Chester's Beethoven lists percussion alongside the
    timpani and Boosey's does not. Each is read as what it says."""
    chester, boosey = (parse_shorthand(text) for text in BEETHOVEN_5)
    assert chester is not None and boosey is not None
    assert "percussion" in chester.instruments
    assert "percussion" not in boosey.instruments


def test_a_readable_shorthand_leaves_nothing_unparsed() -> None:
    for work in (MOZART_25, BEETHOVEN_5, PICTURES, MAHLER_1, RITE):
        for text in work:
            parsed = parse_shorthand(text)
            assert parsed is not None
            assert parsed.unparsed == ()


def test_the_structure_is_json_ready_for_the_raw_payload() -> None:
    parsed = parse_shorthand(BEETHOVEN_5[1])
    assert parsed is not None
    assert parsed.as_dict() == {
        "instruments": [
            "flute",
            "oboe",
            "clarinet",
            "bassoon",
            "horn",
            "trumpet",
            "trombone",
            "timpani",
            "strings",
        ],
        "counts": {
            "flute": 3,
            "oboe": 2,
            "clarinet": 2,
            "bassoon": 3,
            "horn": 2,
            "trumpet": 2,
            "trombone": 3,
            "timpani": 1,
        },
        "string_parts": 6,
    }


@pytest.mark.parametrize(
    "raw",
    [
        # Prose always names an instrument after a count, so it can never present
        # four bare desks — which is what keeps these on the prose path.
        "flute, 2 oboes, 2 clarinets, 2 bassoons, 2 horns, 2 trumpets, timpani, strings",
        "4 horns, 3 trumpets, 3 trombones, 1 tuba, strings",
        "Violine und Klavier",
        "for string orchestra",
        "for piano solo",
        "Urtext Edition, paperbound",
        # Desks but no strings named, and strings named but no desks: a shorthand
        # has to show both before it is read as one.
        "3.2.2.3 - 2.2.3.0",
        "strings[6]",
        "",
        "   ",
    ],
)
def test_what_is_not_shorthand_is_refused(raw: str) -> None:
    """A false positive would file a work under an ensemble it was never written
    for, so the gate is strict and the caller falls back to reading prose."""
    assert parse_shorthand(raw) is None
