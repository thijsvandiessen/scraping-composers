"""Tests for the rohcollections.org.uk page parsers.

The HTML constants are trimmed from real pages, keeping the markup quirks each
test names — the mixed tag case, the ``<br>``-separated credit notes, the
uppercase fixed rows beside lowercase generated ones — and dropping the ~25KB of
navigation, inline SVG and footer every page otherwise carries.
"""

from __future__ import annotations

from composer_scrapers.roh.credits import PROFESSIONS, role
from composer_scrapers.roh.dates import iso_date, place
from composer_scrapers.roh.index import entries
from composer_scrapers.roh.listings import performances, productions
from composer_scrapers.roh.performances import parse as parse_performance
from composer_scrapers.roh.productions import parse as parse_production
from composer_scrapers.roh.text import body, credit, label, pairs, table, text
from composer_scrapers.roh.urls import external_id, index_url, page_ref, work_url
from composer_scrapers.roh.works import parse as parse_work


def panel(inner: str) -> str:
    """A record page: the content panel, then the phone sidebar and footer it must ignore."""
    return f"""<html><body><header><table class="results"><tr><td>nav</td></tr></table></header>
<div id="ContentPlaceHolderBody_uiPanelShowData">{inner}</div>
<div id="sidebarwrapperphone"><table class="result work"><tr><th>Composer:</th>
<td>Not A Composer</td></tr></table></div>
<footer><table class="results production"><tr><td>footer</td></tr></table></footer></body></html>"""


# ---- urls ---- #


def test_an_id_is_read_out_of_a_link_and_its_navigation_state_thrown_away() -> None:
    """The same performance is linked from every search that reaches it."""
    from_search = "performance.aspx?performance=9063&row=1&searchtype=performance&genre=Opera&page=0"
    from_next = "performance.aspx?row=1&amp;page=0&amp;performance=17234"
    absolute = "https://www.rohcollections.org.uk:443/performance.aspx?genre=Opera&performance=3696&row=0"
    assert page_ref(from_search) == ("performance", 9063)
    assert page_ref(from_next) == ("performance", 17234)
    assert page_ref(absolute) == ("performance", 3696)


def test_a_link_is_matched_whatever_case_the_template_wrote_it_in() -> None:
    """The "List All" back-link says Work.aspx; the index says work.aspx."""
    assert page_ref("Work.aspx?work=551") == ("work", 551)
    assert page_ref("work.aspx?work=551") == ("work", 551)
    assert page_ref("Production.aspx?production=16972") == ("production", 16972)


def test_the_pages_this_source_does_not_read_are_not_ids() -> None:
    assert page_ref("relatedobjects.aspx?work=551&collection=Poster Collection") is None
    assert page_ref("performanceindex.aspx?genre=All&letter=A") is None
    assert page_ref("performancesearch.aspx") is None
    assert page_ref("/ContactUs.aspx") is None


def test_a_page_name_that_disagrees_with_its_id_parameter_is_not_an_id() -> None:
    """A mismatch means the pattern caught something this parser does not understand."""
    assert page_ref("work.aspx?production=3775") is None


def test_a_built_url_carries_the_id_and_nothing_else() -> None:
    assert work_url(551) == "https://www.rohcollections.org.uk/work.aspx?work=551"
    assert index_url("0-9").endswith("performanceindex.aspx?genre=All&letter=0-9")
    assert external_id("performance", 18316) == "performance/18316"


# ---- text ---- #


def test_a_label_is_normalised_however_the_template_punctuated_it() -> None:
    """Fixed rows carry a colon, generated ones may not, and one puts it on its own line."""
    assert label("<th>Company:</th>") == "Company"
    assert label("<th>\n Conductor\n</th>") == "Conductor"
    assert label("<th>\n Television - future relay\n :\n</th>") == "Television - future relay"


def test_a_credit_is_split_from_the_note_hanging_off_its_break() -> None:
    """Without the split the note reads as part of the person's name."""
    assert credit("David Edwards <br />(1995 revival)") == ("David Edwards", "(1995 revival)")
    assert credit("Adolph Kvapp <br />For the Mariinsky Theatre, 1900") == (
        "Adolph Kvapp",
        "For the Mariinsky Theatre, 1900",
    )
    assert credit("Vladimir Ponomarev ") == ("Vladimir Ponomarev", None)
    assert credit("") == ("", None)


def test_tags_are_dropped_rather_than_spaced() -> None:
    """Spacing them would put a space before every comma in a title."""
    assert text("<td><b>Boris Godunov</b> [1896]</td>") == "Boris Godunov [1896]"
    assert text("Aida&nbsp;(1871)") == "Aida (1871)"


def test_the_panel_excludes_the_navigation_and_the_repeated_phone_sidebar() -> None:
    """The sidebar repeats the record's own markup and would double every field."""
    read = body(panel('<table class="result work"><tr><th>Composer:</th><td>Verdi</td></tr></table>'))
    assert pairs(table(read, "result", "work") or "") == [("Composer", "Verdi")]
    assert "Not A Composer" not in read


def test_a_class_request_matches_a_token_not_a_string() -> None:
    assert table('<TABLE class="result work" border=0><tr><th>A:</th><td>b</td></tr></TABLE>', "result")
    assert table("<table class=results><tr><td>x</td></tr></table>", "results")


def test_a_narrower_class_can_be_excluded_from_a_wider_one() -> None:
    """A performance page carries "result" and "result work" as two different tables."""
    two = (
        '<table class="result"><tr><th>Venue:</th><td>ROH</td></tr></table>'
        '<table class="result work"><tr><th>Language:</th><td>Italian</td></tr></table>'
    )
    assert pairs(table(two, "result", without="work") or "") == [("Venue", "ROH")]


# ---- credit roles ---- #


def test_a_qualified_job_title_folds_onto_the_job() -> None:
    """Listing every combination would be a hundred entries and still miss the next."""
    assert role("Revival associate director") == "director"
    assert role("Assistant to the choreographer") == "choreographer"
    assert role("Revival lighting designer") == "lighting_designer"


def test_a_qualifier_in_the_middle_of_a_title_is_not_peeled() -> None:
    """ "Combat sequences director" is a different job, not a qualified one."""
    assert role("Combat sequences director") == "combat_sequences_director"


def test_a_trailing_by_folds_onto_the_job_it_names() -> None:
    """ "Devised by", "Scenario by" and "Devised and produced by" are the same grammar."""
    assert role("Scenario by") == "scenario"
    assert role("Staged by") == role("Staging") == "staging"
    assert role("Devised and produced by") == "devised_and_produced"


def test_house_style_variants_of_one_credit_fold_together() -> None:
    """Otherwise one person is two, by which cataloguer typed the row."""
    assert role("Choreography") == role("Choreographer") == "choreographer"
    assert role("Designer") == role("Set designer") == "set_designer"


def test_a_profession_is_claimed_only_where_the_label_states_one() -> None:
    assert PROFESSIONS[role("Composer")] == "composer"
    assert PROFESSIONS[role("Conductor")] == "conductor"
    assert role("Lighting designer") not in PROFESSIONS
    assert role("Combat sequences director") not in PROFESSIONS


# ---- dates ---- #


def test_a_date_keeps_the_precision_the_archive_stated() -> None:
    """A bare year padded to 1 January is indistinguishable from a real one."""
    assert iso_date("14 January 1947") == "1947-01-14"
    assert iso_date("March 1965") == "1965-03"
    assert iso_date("1941") == "1941"


def test_a_premiere_splits_into_its_date_and_its_place() -> None:
    stated = "24 December 1871, Opera House, Cairo"
    assert iso_date(stated) == "1871-12-24"
    assert place(stated) == "Opera House, Cairo"


def test_free_text_that_states_no_date_yields_no_date() -> None:
    """Reading the first four digits of a sentence would find catalogue numbers."""
    assert iso_date("Information on premiere not readily available") is None
    assert iso_date(None) is None
    assert place("1941") is None


# ---- the index ---- #

INDEX = """<table class="results">
<tr><th>Genre</th><th>Title</th></tr>
<tr class="odd"><td class="genre">Ballet</td>
<td class="title"><a href='work.aspx?work=9&amp;row=0&amp;letter=A&amp;'>A new pas de deux</a>
(Kenneth MacMillan)</td></tr>
<tr class="even"><td class="genre">Opera</td>
<td class="title"><a href='work.aspx?work=6924&amp;row=2&amp;letter=A&amp;'>Acis and Galatea</a>
(George Frideric Handel)</td></tr>
<tr class="odd"><td class="genre">Ballet</td>
<td class="title"><a href='work.aspx?work=837&amp;row=3&amp;letter=A&amp;'>La Bayad&#232;re (1941)</a>
(Marius Petipa, Vladimir Ponomarev, Vakhtang Chabukiani)</td></tr>
<tr class="even"><td class="genre">Opera</td>
<td class="title"><a href='work.aspx?work=12194&amp;row=4&amp;letter=B&amp;'>The Barber of Seville:
see 'Il barbiere di Siviglia' (under B in Search by Title)</a></td></tr>
</table>"""


def test_the_index_yields_every_work_with_its_genre() -> None:
    listing = entries(INDEX)
    assert [entry.work_id for entry in listing] == [9, 6924, 837, 12194]
    assert [entry.genre for entry in listing] == ["Ballet", "Opera", "Ballet", "Opera"]
    assert listing[1].title == "Acis and Galatea"


def test_the_bracketed_name_is_read_but_is_not_a_composer() -> None:
    """For a ballet it is the choreographer — MacMillan's pas de deux is Stravinsky's music."""
    listing = entries(INDEX)
    assert listing[0].credited == ("Kenneth MacMillan",)
    assert listing[1].credited == ("George Frideric Handel",)
    assert listing[2].credited == ("Marius Petipa", "Vladimir Ponomarev", "Vakhtang Chabukiani")


def test_a_bracket_inside_the_title_is_not_read_as_a_credit() -> None:
    assert entries(INDEX)[2].title == "La Bayadère (1941)"


def test_a_cross_reference_row_is_left_in_the_listing() -> None:
    """Their wording varies too much to filter on; the work page says what they are."""
    assert entries(INDEX)[3].work_id == 12194
    assert entries(INDEX)[3].credited == ()


# ---- the work page ---- #

BALLET_WORK = panel("""<H1 class=large>Adagio Hammerklavier</H1>
<H2>Ballet: Work details</H2>
<TABLE class="result work" border=0>
<tr><th>Choreographer:</th><td>Hans van Manen </td></tr>
<tr><th>Composer:</th><td>Ludwig van Beethoven </td></tr>
<tr><th>Music title:</th><td>Piano Sonata No. 29 in B-flat, Op. 106 - Slow movement</td></tr>
<TR><TH>Work definition:</TH><TD>Ballet in one act</TD></tr>
<TR><TH>World premiere:</TH><TD>4 October 1973, Dutch National Ballet, Stadsschouwburg, Amsterdam</TD></tr>
</TABLE>
<div class="productionresultsNA"><table class="results production">
<tr><th colspan="2">Productions</th></tr>
<tr class="odd"><td><a href='production.aspx?production=4310&amp;row=0'>Adagio Hammerklavier (1976)</a></td>
<td>The Royal Ballet (3 performances online)</td></tr>
</table></div>""")

OPERA_WORK = panel("""<H1 class=large>Aida</H1>
<div class="relatedrecords"><H2>Related Records</H2>
<a href='relatedobjects.aspx?work=551&amp;collection=Poster Collection'>Poster Collection</a> (71)
</div>
<H2>Opera: Work details</H2>
<TABLE class="result work" border=0>
<tr><th>Composer:</th><td>Giuseppe Verdi </td></tr>
<tr><th>Music title:</th><td>Aida (1871)</td></tr>
<tr><th>Librettist:</th><td>Antonio Ghislanzoni </td></tr>
<TR><TH>ROH premiere:</TH><TD>22 June 1876, The Royal Italian Opera</TD></tr>
</TABLE>
<div class="performanceresults"><table class="results performance">
<tr><th colspan="3">Performances not linked to a production</th></tr>
<tr class="odd"><td><a href='performance.aspx?performance=18316'>28 June 1988</a></td>
<td>Evening </td><td>Royal Opera House, Covent Garden, London</td></tr>
<tr class="even"><td><a href='performance.aspx?performance=18331'>6 July 1988</a></td>
<td>Evening </td><td>Royal Opera House, Covent Garden, London</td></tr>
</table></div>""")

STUB_WORK = panel("""<H1 class=large>The Barber of Seville: see 'Il barbiere di Siviglia'</H1>
<H2>Opera: Work details</H2>
<TABLE class="result work" border=0>
</TABLE>""")

MULTI_WORK = panel("""<H1 class="large">Boris Godunov [1896]</H1>
<H2>Opera: Work details</H2>
<TABLE class="result work" border=0>
<tr><th>Composer:</th><td>Modest Petrovich Musorgsky </td></tr>
<tr><th>Composer:</th><td>Nikolay Andreyevich Rimsky-Korsakov </td></tr>
<tr><th>Librettist:</th><td>Modest Petrovich Musorgsky </td></tr>
<TR><TH>After/based on:</TH><TD>After the play by Pushkin</TD></tr>
</TABLE>""")


def test_a_ballets_composer_is_paired_with_the_music_not_the_staging() -> None:
    """ "Adagio Hammerklavier" is van Manen's; Beethoven wrote the sonata."""
    work = parse_work(301, BALLET_WORK)
    assert work.composers == ("Ludwig van Beethoven",)
    assert work.title == "Adagio Hammerklavier"
    assert work.work_title == "Piano Sonata No. 29 in B-flat, Op. 106 - Slow movement"


def test_a_work_with_no_music_title_pairs_the_composer_with_its_own_title() -> None:
    work = parse_work(1435, MULTI_WORK)
    assert work.work_title == "Boris Godunov [1896]"


def test_every_credit_of_one_role_is_kept_in_order() -> None:
    work = parse_work(1435, MULTI_WORK)
    assert work.composers == ("Modest Petrovich Musorgsky", "Nikolay Andreyevich Rimsky-Korsakov")


def test_the_genre_comes_from_the_details_heading_not_the_first_one() -> None:
    """Works with archival objects emit a "Related Records" heading ahead of it."""
    assert parse_work(551, OPERA_WORK).genre == "Opera"
    assert parse_work(301, BALLET_WORK).genre == "Ballet"


def test_a_cross_reference_stub_is_recognised_by_its_empty_table() -> None:
    stub = parse_work(12194, STUB_WORK)
    assert stub.is_stub
    assert stub.composers == ()
    assert not parse_work(301, BALLET_WORK).is_stub


def test_a_works_productions_carry_the_count_the_site_advertises() -> None:
    refs = parse_work(301, BALLET_WORK).productions
    assert [(ref.production_id, ref.companies, ref.performances) for ref in refs] == [
        (4310, "The Royal Ballet", 3)
    ]


def test_performances_hanging_off_a_work_are_not_lost() -> None:
    """A walk that only descends through productions drops these silently."""
    unlinked = parse_work(551, OPERA_WORK).unlinked
    assert [ref.performance_id for ref in unlinked] == [18316, 18331]
    assert unlinked[0].date == "28 June 1988"
    assert unlinked[0].venue == "Royal Opera House, Covent Garden, London"


def test_the_archival_collections_are_recorded() -> None:
    assert parse_work(551, OPERA_WORK).collections == (("Poster Collection", 71),)


def test_a_label_in_neither_vocabulary_is_reported_rather_than_guessed_at() -> None:
    """An unknown label costs a credit, never a person invented from a fact."""
    assert parse_work(1435, MULTI_WORK).unknown == frozenset()
    odd = parse_work(
        1,
        panel(
            '<H2>Opera: Work details</H2><TABLE class="result work">'
            "<tr><th>Bandmaster:</th><td>A Person</td></tr></TABLE>"
        ),
    )
    assert odd.unknown == {"bandmaster"}
    assert odd.composers == ()


# ---- the production page ---- #

PRODUCTION = panel("""<H1 class=large>La Bayad&#232;re (1941)</H1>
<H2>Ballet: Production details</H2>
<TABLE class="result work" border=0>
<TR><TH>Company:</TH><TD>The Kirov Ballet</TD></tr>
<TR><TH>Production premiere:</TH><TD>1941</TD></tr>
<tr><th>Set designer:</th><td>Adolph Kvapp <br />For the Mariinsky Theatre, 1900</td></tr>
<tr><th>Revival lighting designer:</th><td>Vladimir Lukasevitch <br />(2000 revival)</td></tr>
<tr><th>Additional music:</th><td>Ian Page <br />(recitatives)</td></tr>
<tr><th>Co-production with:</th><td>San Francisco Opera</td></tr>
<TR><TH>Palau de les Arts Reina Sofia, Valencia:</TH><TD>Valencia Palau de les Arts</TD></tr>
<tr><th>Sponsor:</th><td>Anonymous donors; Simon Robertson; Ian Taylor</td></tr>
<TR><TH>Notes:</TH><TD>Used at the Royal Opera House in 2000, 2005 and 2011<br /></TD></tr>
</TABLE>
<table class="results performance">
<tr><th colspan="3">Performances</th></tr>
<tr class="odd"><td><a href='performance.aspx?performance=11827&amp;row=0'>16 June 1994</a></td>
<td>Evening 7.30pm</td><td>Royal Opera House, Covent Garden, London</td></tr>
</table>""")


def test_a_production_is_read_as_a_child_of_the_work_that_named_it() -> None:
    """The page carries no link upward and no work id anywhere in its markup."""
    production = parse_production(7958, 837, PRODUCTION)
    assert production.work_id == 837
    assert production.production_id == 7958
    assert "work.aspx" not in PRODUCTION


def test_a_stagings_credits_are_read_with_their_notes() -> None:
    credits = parse_production(7958, 837, PRODUCTION).credits
    assert (credits[0].role, credits[0].name, credits[0].note) == (
        "set_designer",
        "Adolph Kvapp",
        "For the Mariinsky Theatre, 1900",
    )
    assert (credits[1].role, credits[1].label) == ("lighting_designer", "Revival lighting designer")


def test_a_sponsor_list_is_not_parsed_as_a_person() -> None:
    """It is a semicolon-separated fundraising list, not an artistic credit."""
    production = parse_production(7958, 837, PRODUCTION)
    assert all(c.role != "sponsor" for c in production.credits)
    assert production.fields["Sponsor"].startswith("Anonymous donors;")


def test_a_staging_can_credit_a_composer_the_work_page_never_names() -> None:
    """ "Additional music: Ian Page (recitatives)" is a composer credit."""
    production = parse_production(7958, 837, PRODUCTION)
    added = next(c for c in production.credits if c.role == "composer")
    assert (added.name, added.label, added.note) == ("Ian Page", "Additional music", "(recitatives)")


def test_a_co_production_partner_is_a_fact_not_a_person() -> None:
    """It always names an opera house; read as a credit it becomes a fake person."""
    production = parse_production(7958, 837, PRODUCTION)
    assert all(c.name != "San Francisco Opera" for c in production.credits)
    assert production.fields["Co-production with"] == "San Francisco Opera"


def test_a_label_that_is_itself_a_venue_name_is_reported_not_credited() -> None:
    """Some partner rows are filed under the partner's own name; no list can hold those."""
    production = parse_production(7958, 837, PRODUCTION)
    assert production.unknown == {"palau_de_les_arts_reina_sofia_valencia"}
    assert all("Palau" not in c.name for c in production.credits)


def test_a_production_lists_its_own_nights() -> None:
    refs = parse_production(7958, 837, PRODUCTION).performances
    assert [(ref.performance_id, ref.date, ref.time) for ref in refs] == [
        (11827, "16 June 1994", "Evening 7.30pm")
    ]


def test_the_last_row_of_a_listing_is_not_dropped() -> None:
    """The rows are read out of a table whose closing tag has already been cut."""
    assert len(performances(body(PRODUCTION))) == 1
    assert len(productions(body(BALLET_WORK))) == 1


# ---- the performance page ---- #

PERFORMANCE = panel("""<H1 class="large">Aida - Act IV scene 1 'L'abborrita rivale'-28 June 1988
Evening </H1>
<P class="prevnext"><a id="ContentPlaceHolderBody_uiPerfLinkAll" href="Work.aspx?work=551">List All</a></P>
<H2>Opera:
Performance details</H2>
<TABLE class="result">
<TR><TH>
Venue:</TH><TD>Royal Opera House, Covent Garden, London</TD></tr>
<TR><TH>
Performance status:</TH><TD>Concert</TD></tr>
<tr><th>
Conductor
</th><td>John Barker
</td></tr>
</TABLE>
<div class="personresults"><table class="results performance">
<tr><th colspan="3">Cast</th></tr>
<tr class="odd"><td>Galatea<i>a nymph and demi-goddess</i></td><td></td><td>Danielle de Niese</td></tr>
<tr class="even"><td>ACT II<i></i></td><td></td><td></td></tr>
<tr class="odd"><td><i></i></td><td></td><td>The Orchestra of the Royal Opera House</td></tr>
</table></div>
<TABLE class="result work">
<TR><TH>
Language:</TH><TD>Italian</TD></tr>
<tr><th>
Television - future relay
:
</th><td>BBC 4; 15 May 2009
</td></tr>
</TABLE>""")


def test_the_title_is_cut_at_the_date_the_listing_already_stated() -> None:
    """Titles contain hyphens, so the heading cannot be split on one."""
    performance = parse_performance(18316, 551, None, PERFORMANCE, "28 June 1988")
    assert performance.title == "Aida - Act IV scene 1 'L'abborrita rivale'"


def test_an_unknown_date_leaves_the_heading_whole() -> None:
    """Better a title with a date stuck to it than one truncated at the wrong hyphen."""
    performance = parse_performance(18316, 551, None, PERFORMANCE, "")
    assert performance.title.endswith("28 June 1988 Evening")


def test_both_detail_tables_are_read_without_being_confused() -> None:
    """ "result" and "result work" are two tables on this page, not one."""
    performance = parse_performance(18316, 551, None, PERFORMANCE, "28 June 1988")
    assert performance.venue == "Royal Opera House, Covent Garden, London"
    assert performance.fields["Language"] == "Italian"
    assert performance.fields["Television - future relay"] == "BBC 4; 15 May 2009"
    assert performance.conductors == ("John Barker",)


def test_a_cast_row_that_names_no_one_is_kept_but_makes_nobody() -> None:
    """The table doubles as the running order: "ACT II" is a marker, not a person."""
    performance = parse_performance(18316, 551, None, PERFORMANCE, "28 June 1988")
    assert [entry.role for entry in performance.cast] == ["Galatea", "ACT II", ""]
    assert [entry.name for entry in performance.performers] == [
        "Danielle de Niese",
        "The Orchestra of the Royal Opera House",
    ]


def test_the_italic_gloss_is_a_character_not_part_of_the_part() -> None:
    performance = parse_performance(18316, 551, None, PERFORMANCE, "28 June 1988")
    assert performance.cast[0].character == "a nymph and demi-goddess"
    assert performance.cast[1].character is None


def test_the_pages_own_back_link_is_read_as_a_cross_check() -> None:
    performance = parse_performance(18316, 551, None, PERFORMANCE, "28 June 1988")
    assert performance.parent == ("work", 551)


def test_a_performance_page_names_no_composer() -> None:
    """The fact the whole source is shaped around."""
    assert "Verdi" not in PERFORMANCE
    performance = parse_performance(18316, 551, None, PERFORMANCE, "28 June 1988")
    assert all("composer" not in credit.role for credit in performance.credits)
