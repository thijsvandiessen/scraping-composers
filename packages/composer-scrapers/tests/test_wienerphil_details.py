"""Tests for parsing one Vienna Philharmonic concert detail page.

The page is the only place the archive says what a performer *did*, so what
matters here is the credit labels: which of them mean conducting, which name an
ensemble rather than a musician, and which are the instrument or voice that ends
up on the mention as a discipline.
"""

from __future__ import annotations

from composer_scrapers.wienerphil.details import ConcertDetail, detail


def _entry(label: str, name: str) -> str:
    return (
        f'<div class="entry"><span class="subhead">{label}</span>'
        f'<span class="subline primary-color">{name}</span></div>'
    )


def _programme(*spans: str) -> str:
    return '<div class="entry"><span class="subhead">Program</span>' + "".join(spans) + "</div>"


def _composer(name: str) -> str:
    return f'<span class="subline primary-color">{name}</span>'


def _work(title: str) -> str:
    return f'<span class="subline primary-color cast-programm"><em>{title}</em></span>'


INTERMISSION = '<span class="subline primary-color cast-programm pause">-- INTERMISSION --</span>'


def _page(*blocks: str) -> str:
    return (
        '<div class="grid-x grid-padding-x align-center programm-info event">'
        '<div class="cell medium-5 grid-x align-right"><div class="cell medium-11 large-10">'
        + "".join(blocks)
        + "</div></div></div><footer>the rest of the page</footer>"
    )


PAGE = _page(
    _entry("Conductor", "Otto Nicolai"),
    _entry("Orchestra", "Vienna Philharmonic"),
    _entry("Soprano", "Jenny Lutzer"),
    _entry("Violin", "Joseph Mayseder"),
    # an unlabelled credit: the archive leaves the subhead empty now and then
    _entry("", "Wiener Kammerchor"),
    _entry("", ""),
    _programme(
        _composer("Wolfgang Amadeus Mozart"),
        _work("Symphony [No. 40] in G Minor, K. 550"),
        INTERMISSION,
        _composer("Ludwig van Beethoven"),
        _work("Symphony No. 5 in C Minor, op. 67"),
    ),
)


def read() -> ConcertDetail:
    found = detail(PAGE)
    assert found is not None
    return found


def test_page_without_the_block_is_not_a_concert() -> None:
    assert detail("<html><body>page not found</body></html>") is None


def test_credits_are_kept_verbatim_and_in_page_order() -> None:
    assert read().credits == (
        ("Conductor", "Otto Nicolai"),
        ("Orchestra", "Vienna Philharmonic"),
        ("Soprano", "Jenny Lutzer"),
        ("Violin", "Joseph Mayseder"),
        ("", "Wiener Kammerchor"),
    )


def test_soloists_carry_the_label_as_their_discipline() -> None:
    assert read().soloists == (("Jenny Lutzer", "Soprano"), ("Joseph Mayseder", "Violin"))


def test_ensembles_come_from_the_label_or_the_name() -> None:
    # "Orchestra" is an ensemble by its label; the choir by its name, which is
    # all an unlabelled credit gives us to go on
    assert read().ensembles == ("Vienna Philharmonic", "Wiener Kammerchor")


def test_programme_pairs_each_work_with_the_composer_above_it() -> None:
    assert read().programme == (
        ("Wolfgang Amadeus Mozart", "Symphony [No. 40] in G Minor, K. 550"),
        ("Ludwig van Beethoven", "Symphony No. 5 in C Minor, op. 67"),
    )


def test_a_work_with_no_composer_of_its_own_continues_the_previous_one() -> None:
    page = _page(_programme(_composer("Franz Schubert"), _work("Rosamunde"), _work("Der Erlkönig")))
    assert detail(page).programme == (  # pyright: ignore[reportOptionalMemberAccess]
        ("Franz Schubert", "Rosamunde"),
        ("Franz Schubert", "Der Erlkönig"),
    )


def test_a_german_conducting_label_still_reads_as_conducting() -> None:
    # concert 9546 credits "Musikalische Leitung" and the result listing, which
    # only renders CONDUCTOR blocks, credits nobody at all
    found = detail(_page(_entry("Musikalische Leitung", "Rainer Honeck")))
    assert found is not None
    assert found.conductors == ("Rainer Honeck",)
    assert found.soloists == ()


def test_a_combined_label_conducts_and_plays() -> None:
    # concert 8056: Barenboim is credited twice, once under each label
    found = detail(
        _page(
            _entry("Conductor", "Daniel Barenboim"),
            _entry("Conductor and Piano Soloist", "Daniel Barenboim"),
        )
    )
    assert found is not None
    assert found.conductors == ("Daniel Barenboim",)
    assert found.soloists == (("Daniel Barenboim", "Conductor and Piano Soloist"),)


def test_other_dates_do_not_leak_into_the_concert() -> None:
    # a detail page lists the run's other dates as event-modules — the same
    # markup the result fragments use, with their own credits
    page = PAGE.replace(
        "<footer>",
        '<div class="grid-container more-events-container">'
        '<div class="event-module"data-composers="Richard Strauss;" data-date="2026-08-02">'
        '<div class="c cell small-6"><h3>CONDUCTOR</h3><p>Manfred Honeck</p></div>'
        '<div class="entry"><span class="subhead">Piano</span>'
        '<span class="subline primary-color">Someone Else</span></div>'
        "</div></div><footer>",
    )
    assert detail(page) == read()
