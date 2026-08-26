"""Tests for parsing the Vienna Philharmonic concert archive fragments."""

from __future__ import annotations

import logging

import pytest
from composer_scrapers.wienerphil.details import ConcertDetail
from composer_scrapers.wienerphil.dropdowns import (
    COMPOSERS,
    PERFORMERS,
    VENUES,
    WORKS,
    composer_record,
    performer_record,
    vocabularies,
)
from composer_scrapers.wienerphil.performances import Concert, concerts, mentions, merge, programme

# The archive's own filter vocabulary, rendered twice (desktop and mobile). Only
# the titles matter to the parser: they are what rejoins a title split across
# fragments by the ';' inside it.
SEMICOLON_TITLE = "Pr&auml;ludium, Andante u. Gavotte f&uuml;r Violine; orchestr. v. S. Bachrich"


def _filter_list(name: str, *options: str) -> str:
    items = "".join(
        f'<li class="filter-result-list-li"><a href="javascript:;"'
        f' class="filter-result-list-a" title="{option}"><span>{option}</span></a></li>'
        for option in options
    )
    return f'<div><ul filter-list="{name}" class="filter-result-list-ul">{items}</ul></div>'


LANDING = (
    _filter_list("komponist", "Ludwig van Beethoven", "Arnold Sch&ouml;nberg")
    + _filter_list("werk", SEMICOLON_TITLE, "Symphony No. 8 in F Major, op. 93")
    + _filter_list("interpret", "Otto Nicolai", "Jenny Lutzer", "Vienna Philharmonic")
    + _filter_list("spielort", "Musikverein, Golden Hall, Vienna, Austria")
    # every filter is rendered a second time for the mobile layout
    + _filter_list("komponist", "Ludwig van Beethoven")
)

TITLES = frozenset(vocabularies(LANDING)[WORKS])

# Trimmed copies of the real block structure: the opening tag runs straight into
# the first attribute, entities are escaped, the trailing ';' terminates each
# list, and the credit label is not always English.
FRAGMENT = """\
<div class="event-module"data-title="Philharmonic Concert" \
data-composers="Ludwig van Beethoven;Luigi Cherubini;" \
data-works="Symphony No. 7 in A Major, op. 92;Arie aus der Oper &quot;Fanisca&quot;;" \
data-performers="Otto Nicolai;Jenny Lutzer;Vienna Philharmonic;None;" \
data-location="Hofburg Palace, Vienna, Austria" data-date="1842-03-28">\
<div class="short-date">Mon, March 28, 1842</div>\
<h2><a href="/en/konzerte/philharmonic-concert/2465/" target="_blank">Philharmonic Concert</a></h2>\
<div class="cell h">12:30</div>\
<div class="cell medium-9 event-area">Hofburg Palace, Redoutensaal, Vienna, Austria</div>\
<div class="c cell small-6 xlarge-5"><h3>CONDUCTOR</h3><p>Otto Nicolai</p></div>\
</div>\
<div class="event-module"data-title="Mozart Week Salzburg" \
data-composers="Ludwig van Beethoven;Arnold Sch&ouml;nberg;;" \
data-works="Symphony No. 6 in F Major, op. 68;Five Pieces for Orchestra, op. 16;\
Variationen f&uuml;r Orchester, op. 31;" \
data-performers="Daniel Barenboim;" data-location="Musikverein, Vienna, Austria" data-date="2010-01-09">\
<h2><a href="/en/konzerte/5th-subscription-concert/8057/">5th Subscription Concert</a></h2>\
<p class="st" role="doc-subtitle">Mozart Week 2010</p>\
<div class="cell h">0:00</div>\
<div class="cell medium-9 event-area">Musikverein, Golden Hall, Vienna, Austria</div>\
<div class="c cell small-6"><h3>DIRIGENTIN</h3><p>Daniel Barenboim</p></div>\
</div>\
<div class="event-module"data-title="Concert in Graz" \
data-composers="Franz Schubert;Anton Bruckner;" \
data-works="Symphony No. 6 in C Major, D. 589;-- INTERMISSION --;Symphony No. 4 in E-flat Major;" \
data-performers="" data-location="Stefaniensaal, Graz, Austria" data-date="2013-02-14">\
<h2><a href="/en/konzerte/concert-in-graz/9001/">Concert in Graz</a></h2>\
</div>\
"""


def parsed() -> list[Concert]:
    return list(concerts(FRAGMENT, TITLES))


def test_parses_every_block_of_a_fragment() -> None:
    assert [concert.concert_id for concert in parsed()] == ["2465", "8057", "9001"]


def test_concert_identity_and_place() -> None:
    concert = parsed()[0]
    assert concert.url == "https://www.wienerphilharmoniker.at/en/konzerte/philharmonic-concert/2465/"
    assert concert.title == "Philharmonic Concert"
    assert concert.date == "1842-03-28"
    assert concert.time == "12:30"
    # the block's own line is more precise than the data-location attribute
    assert concert.venue == "Hofburg Palace, Redoutensaal, Vienna, Austria"
    assert concert.location == "Hofburg Palace, Vienna, Austria"


def test_programme_pairs_each_work_with_its_composer() -> None:
    assert parsed()[0].programme == (
        ("Ludwig van Beethoven", "Symphony No. 7 in A Major, op. 92"),
        ("Luigi Cherubini", 'Arie aus der Oper "Fanisca"'),
    )


def test_conductor_is_read_from_the_credit_block() -> None:
    concert = parsed()[0]
    assert concert.conductors == ("Otto Nicolai",)
    assert concert.conductor_labels == ("CONDUCTOR",)


def test_german_credit_label_still_reads_as_a_conductor() -> None:
    # the English pages label some conductors in German
    concert = parsed()[1]
    assert concert.conductors == ("Daniel Barenboim",)
    assert concert.conductor_labels == ("DIRIGENTIN",)


def test_concert_without_a_credit_block_has_no_conductor() -> None:
    concert = parsed()[2]
    assert concert.conductors == ()
    assert concert.time is None
    assert concert.venue is None


def test_soloists_are_the_performers_left_over() -> None:
    concert = parsed()[0]
    # Otto Nicolai conducted and the orchestra is an ensemble; only Lutzer is left,
    # with no discipline — the result listing labels nobody but the conductor
    assert concert.soloists == (("Jenny Lutzer", None),)
    assert concert.ensembles == ("Vienna Philharmonic",)


def test_the_literal_none_performer_is_not_a_musician() -> None:
    # the site renders a performer slot it has no performer for as "None"
    concert = parsed()[0]
    assert "None" not in [name for name, _ in concert.soloists]
    assert "None" not in concert.ensembles


def test_subtitle_is_read_where_a_concert_has_one() -> None:
    assert parsed()[1].subtitle == "Mozart Week 2010"
    assert parsed()[0].subtitle is None


def test_blank_composer_slot_continues_the_previous_composer() -> None:
    # "Beethoven;Schoenberg;;" over three works: the third is Schoenberg's too
    assert parsed()[1].programme == (
        ("Ludwig van Beethoven", "Symphony No. 6 in F Major, op. 68"),
        ("Arnold Schönberg", "Five Pieces for Orchestra, op. 16"),
        ("Arnold Schönberg", "Variationen für Orchester, op. 31"),
    )


def test_intermission_is_not_a_work() -> None:
    # dropping it is what keeps Bruckner off Schubert's symphony
    assert parsed()[2].programme == (
        ("Franz Schubert", "Symphony No. 6 in C Major, D. 589"),
        ("Anton Bruckner", "Symphony No. 4 in E-flat Major"),
    )


def test_title_containing_a_semicolon_is_rejoined() -> None:
    works = (
        "Präludium, Andante u. Gavotte für Violine; orchestr. v. S. Bachrich;"
        "Symphony No. 8 in F Major, op. 93;"
    )
    assert programme("Johann Sebastian Bach;Ludwig van Beethoven;", works, TITLES) == [
        ("Johann Sebastian Bach", "Präludium, Andante u. Gavotte für Violine; orchestr. v. S. Bachrich"),
        ("Ludwig van Beethoven", "Symphony No. 8 in F Major, op. 93"),
    ]


def test_unknown_semicolon_title_folds_on_its_leading_space() -> None:
    # the vocabulary lists the German title where the fragment renders English
    works = "String Quartet B-Flat-Major, op.33/4; Hob. III:40, 1.Mov. Allegro Moderato;"
    assert programme("Joseph Haydn;", works, TITLES) == [
        ("Joseph Haydn", "String Quartet B-Flat-Major, op.33/4; Hob. III:40, 1.Mov. Allegro Moderato"),
    ]


def test_programme_that_cannot_be_lined_up_attributes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # a wrong composer is worse than a missing one
    with caplog.at_level(logging.WARNING):
        paired = programme("Ludwig van Beethoven;", "First Work;Second Work;Third Work;", TITLES)
    assert paired == [(None, "First Work"), (None, "Second Work"), (None, "Third Work")]
    assert "does not line up" in caplog.text


def test_mentions_carry_the_concert_context() -> None:
    concert = parsed()[0]
    first, second = mentions(concert)
    assert first.external_id == "perf:2465:0"
    assert second.external_id == "perf:2465:1"
    assert first.composer == "Ludwig van Beethoven"
    assert first.title == "Symphony No. 7 in A Major, op. 92"
    assert first.raw["concert_id"] == "2465"
    assert first.raw["date"] == "1842-03-28"
    assert first.raw["venue"] == "Hofburg Palace, Redoutensaal, Vienna, Austria"
    assert first.raw["url"] == concert.url
    assert first.raw["conductors"] == ["Otto Nicolai"]
    assert first.raw["ensembles"] == ["Vienna Philharmonic"]
    # the archive labels no soloist; disciplines live on the detail pages only
    assert first.raw["soloists"] == [{"name": "Jenny Lutzer", "discipline": None}]


def test_vocabularies_are_read_once_each() -> None:
    vocabulary = vocabularies(LANDING)
    assert sorted(vocabulary) == sorted([COMPOSERS, PERFORMERS, VENUES, WORKS])
    # the mobile copy of the composer filter must not double the list
    assert vocabulary[COMPOSERS] == ["Ludwig van Beethoven", "Arnold Schönberg"]
    assert vocabulary[PERFORMERS] == ["Otto Nicolai", "Jenny Lutzer", "Vienna Philharmonic"]


def test_composer_record_claims_the_profession() -> None:
    record = composer_record("Ludwig van Beethoven")
    assert record.external_id == "komponist:Ludwig van Beethoven"
    assert record.kind == "person"
    assert [(claim.predicate, claim.object_label) for claim in record.claims] == [
        ("has_profession", "composer")
    ]


def test_performer_who_ever_conducted_is_a_conductor() -> None:
    record = performer_record("Otto Nicolai", {"Otto Nicolai"})
    assert record.kind == "person"
    assert [claim.object_label for claim in record.claims] == ["conductor"]


def test_performer_who_never_conducted_is_a_soloist() -> None:
    record = performer_record("Jenny Lutzer", {"Otto Nicolai"})
    assert record.kind == "person"
    assert [claim.object_label for claim in record.claims] == ["soloist"]


def test_ensemble_in_the_performer_list_is_not_a_person() -> None:
    record = performer_record("Vienna Philharmonic", set())
    assert record.kind == "ensemble"
    # an orchestra has no profession to claim
    assert record.claims == ()


# --- folding a concert's own detail page back over the listing's reading ------


def test_merge_takes_the_disciplines_the_listing_could_not_give() -> None:
    merged = merge(
        parsed()[0],
        ConcertDetail(
            credits=(("Conductor", "Otto Nicolai"), ("Soprano", "Jenny Lutzer")),
            conductors=("Otto Nicolai",),
            soloists=(("Jenny Lutzer", "Soprano"),),
            ensembles=(),
            programme=(),
        ),
    )
    assert merged.soloists == (("Jenny Lutzer", "Soprano"),)
    assert merged.credits == (("Conductor", "Otto Nicolai"), ("Soprano", "Jenny Lutzer"))


def test_merge_recovers_a_conductor_the_credit_block_dropped() -> None:
    # the fragments only render CONDUCTOR-headed blocks, so a concert credited
    # "Musikalische Leitung" has no conductor at all until its page is read
    listed = parsed()[2]
    assert listed.conductors == ()
    merged = merge(
        listed,
        ConcertDetail(
            credits=(("Musikalische Leitung", "Rainer Honeck"),),
            conductors=("Rainer Honeck",),
            soloists=(),
            ensembles=(),
            programme=(),
        ),
    )
    assert merged.conductors == ("Rainer Honeck",)


def test_merge_keeps_a_performer_the_detail_page_does_not_name() -> None:
    merged = merge(
        parsed()[0],
        ConcertDetail(credits=(), conductors=(), soloists=(), ensembles=(), programme=()),
    )
    # Lutzer was in the fragment and is not on the page: she keeps her place,
    # with no discipline, rather than disappearing
    assert merged.soloists == (("Jenny Lutzer", None),)


def test_merge_prefers_the_detail_pages_programme() -> None:
    merged = merge(
        parsed()[0],
        ConcertDetail(
            credits=(),
            conductors=(),
            soloists=(),
            ensembles=(),
            programme=(("Ludwig van Beethoven", "Symphony No. 7 in A Major, op. 92"),),
        ),
    )
    assert merged.programme == (("Ludwig van Beethoven", "Symphony No. 7 in A Major, op. 92"),)


def test_merge_falls_back_to_the_listing_when_the_page_has_no_programme() -> None:
    listed = parsed()[0]
    merged = merge(listed, ConcertDetail(credits=(), conductors=(), soloists=(), ensembles=(), programme=()))
    assert merged.programme == listed.programme
