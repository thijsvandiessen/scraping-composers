"""Parsing one IMSLP work page's commercial recordings."""

from __future__ import annotations

from composer_scrapers.imslp_recordings.recordings import (
    commercial_recordings,
    cover_id,
    credits,
    split_page_title,
    work_page,
)

# A page's parser output, trimmed to the shape that matters: the JGCommRec
# global among other IMSLP globals, so the parser has to find where it starts
# and where it ends rather than assuming it stands alone.
PAGE = """
<span id="wpaudiosection"><h2>Performances</h2></span>
<script>JGBSAccompnum=0;JGCommRec=[
 {"aid":61682,
  "tit":"VAUGHAN WILLIAMS, R.: 10 Blake Songs (Banfalvi)",
  "img":"https://cdn.imslp.org/naxoscache.php?pool=hires&file=C5035.jpg",
  "smi":"https://cdn.imslp.org/naxoscache.php?pool=others&file=C5035.gif",
  "art":"William Blake (lyricist), Andreas Weller (tenor), Lajos Lencs\\u00e9s (oboe)",
  "trs":[{"dsc":"10 Blake Songs: No. 1. Infant Joy","dur":106,"url":"gW5pWBXs+token=="},
         {"dsc":"10 Blake Songs: No. 2. A Poison Tree","dur":139,"url":"gW5pWBXs+token=="}]},
 {"aid":74819,
  "tit":"Songs of Travel",
  "img":"http://petruccimusiclibrary.ca/all/12148-bpt6k88103018/coverimage.jpg",
  "art":"Fantazia Szalonzenekar (orchestra), Michael Bojesen (piano, conductor)",
  "trs":[{"dsc":"The Vagabond","dur":null}]}
];JGaskdonation=0;</script>
<div id="naxosscroll">Javascript not enabled.</div>
"""

PAGE_WITHOUT_RECORDINGS = """
<span id="wpaudiosection"><h2>Performances</h2></span>
<script>JGBSAccompnum=0;JGaskdonation=0;</script>
"""


def test_reads_every_album_on_the_page() -> None:
    albums = commercial_recordings(PAGE)
    assert [album.album_id for album in albums] == [61682, 74819]
    assert albums[0].title == "VAUGHAN WILLIAMS, R.: 10 Blake Songs (Banfalvi)"


def test_stops_at_the_end_of_the_array_not_the_end_of_the_script() -> None:
    """The globals after JGCommRec must not be swallowed into it."""
    assert len(commercial_recordings(PAGE)) == 2


def test_keeps_the_tracks_of_this_work_without_their_streaming_tokens() -> None:
    tracks = commercial_recordings(PAGE)[0].tracks
    assert [(track.description, track.duration_s) for track in tracks] == [
        ("10 Blake Songs: No. 1. Infant Joy", 106),
        ("10 Blake Songs: No. 2. A Poison Tree", 139),
    ]


def test_a_null_duration_is_not_a_duration() -> None:
    assert commercial_recordings(PAGE)[1].tracks[0].duration_s is None


def test_a_page_without_recordings_is_empty_not_an_error() -> None:
    assert commercial_recordings(PAGE_WITHOUT_RECORDINGS) == []


def test_an_unreadable_payload_is_empty_not_an_error() -> None:
    assert commercial_recordings("<script>JGCommRec=[{oh dear</script>") == []


def test_an_entry_without_an_album_id_is_skipped() -> None:
    """The album id is the record key downstream; an entry without one is unusable."""
    albums = commercial_recordings('JGCommRec=[{"tit":"Nameless"},{"aid":7}]')
    assert [album.album_id for album in albums] == [7]


class TestCredits:
    def test_a_lyricist_wrote_the_words_and_did_not_play(self) -> None:
        assert [credit.name for credit in credits("William Blake (lyricist), Andreas Weller (tenor)")] == [
            "Andreas Weller"
        ]

    def test_an_instrument_is_a_soloist_and_its_discipline(self) -> None:
        (credit,) = credits("Lajos Lencsés (oboe)")
        assert (credit.role, credit.discipline) == ("soloist", "oboe")

    def test_a_conductor_is_a_conductor(self) -> None:
        (credit,) = credits("Nils Grevillius (conductor)")
        assert (credit.role, credit.discipline) == ("conductor", None)

    def test_conducting_wins_over_playing_but_keeps_the_instrument(self) -> None:
        (credit,) = credits("Michael Bojesen (piano, conductor)")
        assert (credit.role, credit.discipline) == ("conductor", "piano")

    def test_a_role_listing_two_instruments_is_not_two_people(self) -> None:
        """A naive split on ", " invents "François Pilon (violin" and "mandolin)"."""
        (credit,) = credits("François Pilon (violin, mandolin)")
        assert credit.name == "François Pilon"
        assert credit.disciplines == ("violin", "mandolin")

    def test_an_unroled_group_name_is_an_ensemble(self) -> None:
        (credit,) = credits("Royal Philharmonic Orchestra")
        assert (credit.role, credit.discipline) == ("ensemble", None)

    def test_an_unroled_person_is_a_performer(self) -> None:
        (credit,) = credits("Dario Moreno")
        assert (credit.role, credit.discipline) == ("performer", None)

    def test_a_role_naming_a_group_is_not_an_instrument(self) -> None:
        """ "(orchestra)" says what someone is, not what they played."""
        (credit,) = credits("Carlos Dalmonte et son orchestre (orchestra)")
        assert (credit.role, credit.discipline) == ("ensemble", None)

    def test_a_role_repeating_the_group_name_is_not_an_instrument(self) -> None:
        (credit,) = credits("Berlin Radio Symphony Orchestra (Radio-Sinfonie-Orchester Berlin)")
        assert (credit.role, credit.discipline) == ("ensemble", None)

    def test_a_role_that_says_nothing_leaves_the_credit_undisciplined(self) -> None:
        assert [(c.role, c.discipline) for c in credits("Tito Schipa (other), Mario Lanza (singer)")] == [
            ("performer", None),
            ("performer", None),
        ]

    def test_a_featured_group_is_still_a_group(self) -> None:
        (credit,) = credits("Claude Bolling Et Son Orchestre (featuring)")
        assert credit.role == "ensemble"

    def test_a_bare_soloist_role_stays_a_soloist(self) -> None:
        (credit,) = credits("Georges Jouvin (soloist)")
        assert (credit.role, credit.discipline) == ("soloist", None)

    def test_an_empty_credit_line_credits_nobody(self) -> None:
        assert credits("") == ()
        assert credits(None) == ()

    def test_credits_keep_the_order_they_were_named_in(self) -> None:
        line = "Dmitri Hvorostovsky (baritone), Russian Philharmonia, Constantine Orbelian (conductor)"
        assert [credit.role for credit in credits(line)] == ["soloist", "ensemble", "conductor"]


class TestSplitPageTitle:
    def test_splits_the_composer_off_the_title(self) -> None:
        assert split_page_title("10 Blake Songs (Vaughan Williams, Ralph)") == (
            "10 Blake Songs",
            "Vaughan Williams, Ralph",
        )

    def test_a_title_that_starts_with_punctuation_still_splits(self) -> None:
        assert split_page_title("'O sole mio (Di Capua, Eduardo)") == ("'O sole mio", "Di Capua, Eduardo")

    def test_a_title_with_its_own_comma_keeps_it(self) -> None:
        assert split_page_title("12 German Dances, WoO 8 (Beethoven, Ludwig van)") == (
            "12 German Dances, WoO 8",
            "Beethoven, Ludwig van",
        )

    def test_only_the_last_group_is_the_composer(self) -> None:
        assert split_page_title("Fugue (arr. for 2 pianos) (Bach, Johann Sebastian)") == (
            "Fugue (arr. for 2 pianos)",
            "Bach, Johann Sebastian",
        )

    def test_a_trailing_group_without_a_comma_is_part_of_the_title(self) -> None:
        assert split_page_title("Symphony (unfinished)") == ("Symphony (unfinished)", None)

    def test_a_title_with_no_group_has_no_composer(self) -> None:
        assert split_page_title("Nocturne") == ("Nocturne", None)


class TestCoverId:
    def test_reads_the_naxos_catalogue_number_out_of_the_query(self) -> None:
        assert cover_id("https://cdn.imslp.org/naxoscache.php?pool=hires&file=C5035.jpg") == "C5035"

    def test_reads_the_bnf_ark_out_of_the_path(self) -> None:
        """The filename is always "coverimage"; only the directory identifies."""
        assert (
            cover_id("http://petruccimusiclibrary.ca/all/12148-bpt6k88103018/coverimage.jpg")
            == "12148-bpt6k88103018"
        )

    def test_a_cover_of_neither_shape_has_no_id(self) -> None:
        assert cover_id("https://imslp.org/images/thumb.jpg") is None
        assert cover_id(None) is None

    def test_an_album_takes_its_catalogue_number_from_its_cover(self) -> None:
        albums = commercial_recordings(PAGE)
        assert [album.catalogue_number for album in albums] == ["C5035", "12148-bpt6k88103018"]


def test_work_page_carries_both_the_page_title_and_what_it_means() -> None:
    page = work_page(49198, "10 Blake Songs (Vaughan Williams, Ralph)", "https://imslp.org/wiki/x")
    assert (page.page_id, page.title, page.composer) == (
        49198,
        "10 Blake Songs",
        "Vaughan Williams, Ralph",
    )
