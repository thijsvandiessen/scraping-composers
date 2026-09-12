"""Tests for the harmoniamundi page parsers.

The HTML constants are trimmed from real harmoniamundi.com pages, keeping the
markup quirks each test names and dropping the ~120KB of navigation chrome,
inline SVG and lazy-loading ``<picture>`` markup every page otherwise carries.
"""

from __future__ import annotations

from composer_scrapers.harmoniamundi.albums import credits, parse_album, release_date
from composer_scrapers.harmoniamundi.contents import parse_contents
from composer_scrapers.harmoniamundi.people import Roster, parse_profile
from composer_scrapers.harmoniamundi.text import lines, text
from composer_scrapers.harmoniamundi.urls import (
    album_urls,
    child_sitemaps,
    detail_path,
    page_ref,
    profile_ref,
    profile_urls,
    slug,
)

# ---- the sitemaps ---- #

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.harmoniamundi.com/page-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.harmoniamundi.com/artists-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.harmoniamundi.com/composers-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.harmoniamundi.com/albums-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.harmoniamundi.com/albums-sitemap6.xml</loc></sitemap>
  <sitemap><loc>https://www.harmoniamundi.com/concerts-sitemap.xml</loc></sitemap>
</sitemapindex>
"""

# The French pages sit at the bare path and the German ones under /de/; the
# landing page shares its prefix with the detail pages.
ALBUM_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.harmoniamundi.com/en/albums/</loc><lastmod>2026-09-12T01:39:11+00:00</lastmod></url>
  <url><loc>https://www.harmoniamundi.com/en/albums/bach-js-chaconnes/</loc></url>
  <url><loc>https://www.harmoniamundi.com/albums/bach-js-chaconnes/</loc></url>
  <url><loc>https://www.harmoniamundi.com/de/albums/bach-js-chaconnes/</loc></url>
  <url><loc>https://www.harmoniamundi.com/en/albums/brahms-trio-zimmermann/</loc></url>
</urlset>
"""

PROFILE_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.harmoniamundi.com/en/artistes/</loc></url>
  <url><loc>https://www.harmoniamundi.com/en/artistes/juliette-hurel/</loc></url>
  <url><loc>https://www.harmoniamundi.com/en/compositeurs/francois-couperin/</loc></url>
  <url><loc>https://www.harmoniamundi.com/fr/artistes/juliette-hurel/</loc></url>
</urlset>
"""


def test_the_index_separates_album_sitemaps_from_people_sitemaps() -> None:
    albums, people = child_sitemaps(SITEMAP_INDEX)
    assert albums == [
        "https://www.harmoniamundi.com/albums-sitemap.xml",
        "https://www.harmoniamundi.com/albums-sitemap6.xml",
    ]
    assert people == [
        "https://www.harmoniamundi.com/artists-sitemap.xml",
        "https://www.harmoniamundi.com/composers-sitemap.xml",
    ]


def test_the_album_sitemaps_are_matched_by_prefix_not_enumerated() -> None:
    """The numbered set grows with the catalogue; a fixed list would miss releases."""
    index = SITEMAP_INDEX.replace("albums-sitemap6.xml", "albums-sitemap11.xml")
    albums, _ = child_sitemaps(index)
    assert "https://www.harmoniamundi.com/albums-sitemap11.xml" in albums


def test_only_the_english_pages_are_read() -> None:
    """The same release exists in three locales; taking more than one would load it thrice."""
    assert album_urls(ALBUM_URLSET) == [
        "https://www.harmoniamundi.com/en/albums/bach-js-chaconnes/",
        "https://www.harmoniamundi.com/en/albums/brahms-trio-zimmermann/",
    ]


def test_the_section_landing_page_is_not_an_album() -> None:
    assert "https://www.harmoniamundi.com/en/albums/" not in album_urls(ALBUM_URLSET)


def test_artist_and_composer_pages_come_back_together() -> None:
    assert profile_urls(PROFILE_URLSET) == [
        "https://www.harmoniamundi.com/en/artistes/juliette-hurel/",
        "https://www.harmoniamundi.com/en/compositeurs/francois-couperin/",
    ]


def test_every_built_url_ends_in_a_slash() -> None:
    """Without it the site answers 301 and, with redirects off, an empty body."""
    for url in album_urls(ALBUM_URLSET) + profile_urls(PROFILE_URLSET):
        assert url.endswith("/")


def test_a_profile_ref_reads_both_the_artist_and_the_composer_section() -> None:
    assert profile_ref("https://www.harmoniamundi.com/en/artistes/graham-ross/") == (
        "artist",
        "graham-ross",
    )
    assert profile_ref("/en/compositeurs/henry-purcell/") == ("composer", "henry-purcell")


def test_an_album_link_is_not_a_profile_ref() -> None:
    assert profile_ref("https://www.harmoniamundi.com/en/albums/bach-js-chaconnes/") is None
    assert page_ref("https://www.harmoniamundi.com/en/albums/bach-js-chaconnes/") == (
        "album",
        "bach-js-chaconnes",
    )


def test_an_off_site_link_is_not_a_page() -> None:
    assert page_ref("https://www.qobuz.com/album/x") is None
    assert page_ref("") is None


def test_the_id_path_drops_the_locale() -> None:
    """It identifies the record, not the translation."""
    assert detail_path("artist", "graham-ross") == "/artistes/graham-ross"
    assert detail_path("composer", "henry-purcell") == "/compositeurs/henry-purcell"


def test_slug_reads_the_trailing_segment() -> None:
    assert slug("https://www.harmoniamundi.com/en/albums/bach-js-chaconnes/") == "bach-js-chaconnes"


# ---- the rendered text ---- #


def test_a_bolded_fragment_of_a_title_does_not_gain_a_space() -> None:
    """The editors bold part of a title; spacing the tags out breaks the comma."""
    assert text("<b>Fantasia in D Minor</b>, K.397") == "Fantasia in D Minor, K.397"


def test_an_entity_is_resolved_and_a_non_breaking_space_is_folded() -> None:
    assert text("<strong>Chout\xa0</strong>Op. 21 &amp; more") == "Chout Op. 21 & more"


def test_both_line_break_styles_are_honoured() -> None:
    """Older albums separate lines with <br>, newer ones with newlines."""
    assert lines("a<br>b<br />c") == ["a", "b", "c"]
    assert lines("a\n b\nc") == ["a", "b", "c"]


# ---- one album page ---- #

ALBUM_HTML = """
<meta property="og:image" content="https://www.harmoniamundi.com/wp-content/uploads/905406_web.jpg" />
<div class="album_informations">
  <h1 class="album_title">
    <span class="compositeurs">SERGEI PROKOFIEV</span>
    Chout (complete ballet) - Symphony No. 1 'Classical'
    <span class="artistes as_p">NDR Radiophilharmonie, Stanislav Kochanovsky</span>
  </h1>
  <div class="features_group">
    <div class="feature duration"><svg xmlns="http://www.w3.org/2000/svg"><g><rect/></g></svg>
1h09</div>
    <div class="feature cd_number">1 CD</div>
    <div class="feature ref">HMM905406</div>
    <time datetime="" class="feature release_date">October 2026</time>
  </div>
</div>
<div class="album_humans isigrid">
  <div class="col-6"><div class="humans_list">
    <h2>Artists</h2>
    <ul>
      <li>
        <div class="human as_h4">
          <a href="https://www.harmoniamundi.com/en/artistes/ndr-radiophilharmonie/">NDR
          Radiophilharmonie <i class="fal fa-external-link"></i></a>
        </div>
        <div class="role as_h5">Orchestra</div>
      </li>
      <li>
        <div class="human as_h4">
          <a href="https://www.harmoniamundi.com/en/artistes/stanislav-kochanovsky/">Stanislav
          Kochanovsky <i class="fal fa-external-link"></i></a>
        </div>
        <div class="role as_h5">Conductor</div>
      </li>
      <li>
        <div class="human as_h4">Nobuyuki Tsujii</div>
        <div class="role as_h5">Piano</div>
      </li>
      <li>
        <div class="human as_h4">Nobuyuki Tsujii</div>
        <div class="role as_h5">Piano</div>
      </li>
    </ul>
  </div></div>
  <div class="col-6"><div class="humans_list">
    <h2>Composers </h2>
    <ul>
      <li>
        <div class="human as_h4">
          <a href="https://www.harmoniamundi.com/en/compositeurs/sergey-prokofiev/">
          Sergey Prokofiev <i class="fal fa-external-link"></i></a>
        </div>
      </li>
    </ul>
  </div></div>
</div>
<div class="harmonia_mundi_description">
  <h2 class="title">Contents</h2>
  SERGEI PROKOFIEV (1891-1953)
<strong>Chout\xa0</strong>Op. 21
<strong>Tableau 1</strong>
<strong>.</strong>\xa0Andantino scherzando (6'43)
· Allegro irresoluto (3'04)
</div>
"""

ALBUM_URL = "https://www.harmoniamundi.com/en/albums/prokofiev-chout/"


def test_the_album_title_is_the_heading_without_its_two_spans() -> None:
    album = parse_album(ALBUM_HTML, ALBUM_URL)
    assert album is not None
    assert album.title == "Chout (complete ballet) - Symphony No. 1 'Classical'"


def test_the_heading_spans_are_kept_as_display_strings() -> None:
    """They describe the whole release, so they are never mined for entities."""
    album = parse_album(ALBUM_HTML, ALBUM_URL)
    assert album is not None
    assert album.composers_display == "SERGEI PROKOFIEV"
    assert album.artists_display == "NDR Radiophilharmonie, Stanislav Kochanovsky"


def test_anthology_in_the_heading_is_not_a_composer() -> None:
    album = parse_album(ALBUM_HTML.replace("SERGEI PROKOFIEV</span>", "Anthology</span>"), ALBUM_URL)
    assert album is not None
    assert album.composers_display == "Anthology"
    assert [credit.name for credit in album.composer_credits] == ["Sergey Prokofiev"]


def test_the_catalogue_number_and_release_date_are_read() -> None:
    album = parse_album(ALBUM_HTML, ALBUM_URL)
    assert album is not None
    assert album.catalogue_number == "HMM905406"
    assert album.release_date == "2026-10"


def test_the_release_date_is_normalized_because_it_is_ordered_as_text() -> None:
    assert release_date("October 2026") == "2026-10"
    assert release_date("May 2005") == "2005-05"
    assert release_date("2005") == "2005"
    assert release_date("soon") is None


def test_cd_number_is_read_as_a_format_not_a_disc_count() -> None:
    album = parse_album(ALBUM_HTML.replace(">1 CD<", ">Digital<"), ALBUM_URL)
    assert album is not None
    assert album.format == "Digital"


def test_the_duration_is_read_past_its_inline_svg_icon() -> None:
    album = parse_album(ALBUM_HTML, ALBUM_URL)
    assert album is not None
    assert album.duration == "1h09"


def test_a_forthcoming_release_has_no_catalogue_number_rather_than_an_empty_one() -> None:
    """A present-but-empty value would read as a conflicting one in a merge."""
    forthcoming = ALBUM_HTML.replace(">HMM905406<", "><").replace(">1 CD<", "><")
    album = parse_album(forthcoming, ALBUM_URL)
    assert album is not None
    assert album.catalogue_number is None
    assert album.format is None
    assert album.release_date == "2026-10"


def test_a_page_that_is_not_an_album_parses_as_none() -> None:
    assert parse_album("<html><body>the album search</body></html>", ALBUM_URL) is None


# ---- the credits ---- #


def test_a_linked_credit_yields_its_slug_and_a_bare_one_does_not() -> None:
    found = credits(ALBUM_HTML)
    by_name = {credit.name: credit for credit in found}
    assert by_name["Stanislav Kochanovsky"].ref == ("artist", "stanislav-kochanovsky")
    assert by_name["Nobuyuki Tsujii"].ref is None


def test_the_external_link_icon_is_not_part_of_the_name() -> None:
    assert "NDR Radiophilharmonie" in {credit.name for credit in credits(ALBUM_HTML)}


def test_a_credit_repeated_in_one_column_is_kept_once() -> None:
    """Loading it twice would double that person's participation on the recording."""
    assert [credit.name for credit in credits(ALBUM_HTML)].count("Nobuyuki Tsujii") == 1


def test_the_composers_heading_matches_despite_its_trailing_space() -> None:
    """The markup is <h2>Composers </h2>; a literal comparison drops every composer."""
    columns = {credit.column for credit in credits(ALBUM_HTML)}
    assert columns == {"artists", "composers"}


def test_a_composer_entry_has_no_role_div_and_parses_with_an_empty_role() -> None:
    composer = next(credit for credit in credits(ALBUM_HTML) if credit.column == "composers")
    assert composer.role == ""
    assert composer.discipline is None


def test_a_role_that_names_what_someone_is_is_not_a_discipline() -> None:
    by_name = {credit.name: credit for credit in credits(ALBUM_HTML)}
    assert by_name["Stanislav Kochanovsky"].discipline is None
    assert by_name["Nobuyuki Tsujii"].discipline == "Piano"


def test_a_page_with_no_credits_block_yields_no_credits() -> None:
    assert credits("<html><body>nothing here</body></html>") == ()


def test_the_composers_column_is_not_among_the_performers() -> None:
    """A composer is not a performer on their own record."""
    album = parse_album(ALBUM_HTML, ALBUM_URL)
    assert album is not None
    assert "Sergey Prokofiev" not in {credit.name for credit in album.performers}


# ---- the tracklist ---- #


def test_life_dates_open_a_composer_block_in_either_bracket_style() -> None:
    blocks = parse_contents("Johann Sebastian Bach (1685-1750)<br>MOZART [1756-1791]")
    assert [(b.name, b.born, b.died) for b in blocks] == [
        ("Johann Sebastian Bach", "1685", "1750"),
        ("MOZART", "1756", "1791"),
    ]


def test_a_composer_heading_need_not_be_upper_case() -> None:
    """`Johann Sebastian Bach (1685-1750)` appears in title case in the catalogue."""
    blocks = parse_contents("Johann Sebastian Bach (1685-1750)<br>· Chaconne (7'16)")
    assert blocks[0].name == "Johann Sebastian Bach"


def test_a_living_composer_yields_a_birth_year_and_no_death_year() -> None:
    blocks = parse_contents("Nico Muhly (1981 - )<br>· Motet (4'02)")
    assert (blocks[0].born, blocks[0].died) == ("1981", None)


def test_a_heading_carrying_a_bullet_and_a_duration_is_still_a_heading() -> None:
    """`· Traditional, arr. James ERB (1926-2014) (3'53)` occurs verbatim."""
    blocks = parse_contents("· Leonard BERNSTEIN (1918-1990)<br>· Traditional, arr. ERB (1926-2014) (3'53)")
    assert [b.name for b in blocks] == ["Leonard BERNSTEIN", "Traditional, arr. ERB"]


def test_a_qualified_or_full_date_still_reads_as_life_dates() -> None:
    blocks = parse_contents("SIEUR DEMACHY (fl. 1675-1700)<br>GEORG NEUMARK [01/01/1621-01/01/1681]")
    assert [(b.name, b.born, b.died) for b in blocks] == [
        ("SIEUR DEMACHY", "1675", "1700"),
        ("GEORG NEUMARK", "1621", "1681"),
    ]


def test_a_bare_parenthetical_year_does_not_open_a_block() -> None:
    blocks = parse_contents("BACH (1685-1750)<br>Chichester Psalms (1965)<br>· I. (4'03)")
    assert [b.name for b in blocks] == ["BACH"]
    assert blocks[0].works[0].title == "Chichester Psalms (1965)"


def test_a_bullet_is_a_track_and_its_duration_leaves_the_title() -> None:
    blocks = parse_contents("BACH (1685-1750)<br>Suite<br>· I. Allegro (11'02)")
    track = blocks[0].works[0].tracks[0]
    assert (track.title, track.duration) == ("I. Allegro", "11'02")


def test_a_bolded_bullet_is_still_a_track() -> None:
    """Some albums bold every track line; <strong> carries no information here."""
    blocks = parse_contents("KYD (1978-)<br>Collection<br><strong>· Ezio's Family</strong> (3'22)")
    assert blocks[0].works[0].tracks[0].title == "Ezio's Family"


def test_a_dot_bullet_is_a_track_even_without_a_following_space() -> None:
    blocks = parse_contents("BACH (1685-1750)<br>Suite<br>.Moderato scherzando (7'17)")
    assert blocks[0].works[0].tracks[0].title == "Moderato scherzando"


def test_a_heading_broken_over_two_lines_stays_one_work() -> None:
    """The name on one line, the key and catalogue number on the next."""
    blocks = parse_contents(
        "SCHUBERT (1797-1828)<br>Sonata in D major<br>Ré majeur / D-dur D.384<br>· I. Allegro (4'27)"
    )
    work = blocks[0].works[0]
    assert len(blocks[0].works) == 1
    assert work.title == "Sonata in D major"
    assert work.subtitle == "Ré majeur / D-dur D.384"
    assert len(work.tracks) == 1


def test_a_line_that_is_wholly_a_parenthetical_is_a_note_not_a_work() -> None:
    blocks = parse_contents("COUPERIN (1668-1733)<br>· Musette (2'10)<br>(Pièces de clavecin, 1722)")
    assert [work.title for work in blocks[0].works] == ["Musette"]


def test_a_repeated_composer_name_without_dates_reopens_that_block() -> None:
    """The editors repeat the name alone rather than repeating the dates."""
    blocks = parse_contents(
        "CLAUDE DEBUSSY (1862-1918)<br>Estampes<br>· Pagodes (5'01)<br>"
        "CLAUDE DEBUSSY<br>Images<br>· Reflets (4'30)"
    )
    assert [b.name for b in blocks] == ["CLAUDE DEBUSSY", "CLAUDE DEBUSSY"]
    assert [b.works[0].title for b in blocks] == ["Estampes", "Images"]


def test_a_heading_is_respelled_from_the_credited_composer() -> None:
    """Headings are styled upper-case; the credits carry the readable spelling."""
    blocks = parse_contents("CLAUDE DEBUSSY (1862-1918)<br>· Pagodes (5'01)", ["Claude Debussy"])
    assert blocks[0].name == "Claude Debussy"


def test_a_placeholder_attribution_is_not_claimed_as_a_composer() -> None:
    blocks = parse_contents("Anonymous (1500-1600)<br>· Kyrie (3'10)")
    assert blocks[0].name == "Anonymous"
    assert blocks[0].composer is None


def test_tracks_with_no_heading_above_them_each_become_a_work() -> None:
    blocks = parse_contents(
        "DOWLAND (1563-1626)<br>· My lady hunnsdons puffe (1'23)<br>· Solus cum sola (4'25)"
    )
    assert [work.title for work in blocks[0].works] == ["My lady hunnsdons puffe", "Solus cum sola"]


def test_a_blob_with_no_composer_heading_still_yields_its_works() -> None:
    blocks = parse_contents("Suite in G<br>· I. Prelude (2'00)")
    assert len(blocks) == 1
    assert blocks[0].name is None
    assert blocks[0].works[0].title == "Suite in G"


def test_an_album_parses_its_tracklist_and_keeps_it_verbatim() -> None:
    album = parse_album(ALBUM_HTML, ALBUM_URL)
    assert album is not None
    # Not respelled: the page really does write SERGEI in the tracklist and
    # Sergey in the credit, and those are two different names to fold on.
    assert album.contents[0].name == "SERGEI PROKOFIEV"
    assert album.contents[0].works[0].title == "Chout Op. 21"
    tracks = [track.title for work in album.contents[0].works for track in work.tracks]
    assert tracks == ["Andantino scherzando", "Allegro irresoluto"]
    assert album.contents_text is not None
    assert "SERGEI PROKOFIEV (1891-1953)" in album.contents_text


# ---- the profile pages ---- #

ARTIST_HTML = """
<div class="single_humains">
  <h1 class="humain_title">Juliette Hurel</h1>
  <div class="hero_content as_h4">Flute</div>
  <div id="biographie" class="humain_information biographie">
    <div class="global_content">
      <h2>Biography</h2>
      <p>Principal Flute of the Rotterdam Philharmonic Orchestra.</p>
    </div>
  </div>
  <div id="discographie" class="humain_information discographie">
    <a href="https://www.harmoniamundi.com/en/albums/elegance/" class="card_albums">only one card</a>
  </div>
</div>
"""

COMPOSER_HTML = """
<div class="single_humains">
  <h1 class="humain_title">François Couperin</h1>
  <div class="hero_content as_h4">1668 - 1733</div>
</div>
"""


def test_an_artist_page_yields_a_name_and_an_instrument() -> None:
    profile = parse_profile(ARTIST_HTML, "artist", "juliette-hurel")
    assert profile is not None
    assert profile.name == "Juliette Hurel"
    assert profile.disciplines == ["Flute"]
    assert (profile.born, profile.died) == (None, None)


def test_a_biography_is_read_without_its_heading() -> None:
    profile = parse_profile(ARTIST_HTML, "artist", "juliette-hurel")
    assert profile is not None
    assert profile.bio == "Principal Flute of the Rotterdam Philharmonic Orchestra."


def test_a_composer_page_yields_life_dates_and_no_instrument() -> None:
    profile = parse_profile(COMPOSER_HTML, "composer", "francois-couperin")
    assert profile is not None
    assert (profile.born, profile.died) == ("1668", "1733")
    assert profile.disciplines == []
    assert profile.bio is None


def test_a_hero_line_naming_several_instruments_splits_them() -> None:
    profile = parse_profile(
        ARTIST_HTML.replace(">Flute<", ">Soprano, Harpsichord<"), "artist", "juliette-hurel"
    )
    assert profile is not None
    assert profile.disciplines == ["Soprano", "Harpsichord"]


def test_a_page_with_no_title_parses_as_none() -> None:
    assert parse_profile("<html><body>gone</body></html>", "artist", "nobody") is None


def test_the_profile_url_is_canonical() -> None:
    profile = parse_profile(COMPOSER_HTML, "composer", "francois-couperin")
    assert profile is not None
    assert profile.url == "https://www.harmoniamundi.com/en/compositeurs/francois-couperin/"


# ---- the roster ---- #


def _roster(html: str = ALBUM_HTML) -> Roster:
    roster = Roster()
    for credit in credits(html):
        roster.add(credit)
    return roster


def test_a_linked_person_is_keyed_by_their_page_and_a_bare_one_by_their_name() -> None:
    by_name = {person.name: person for person in _roster()}
    assert by_name["Stanislav Kochanovsky"].external_id == "/artistes/stanislav-kochanovsky"
    assert by_name["Nobuyuki Tsujii"].external_id == "/names/Nobuyuki%20Tsujii"


def test_an_external_id_is_the_name_itself_so_it_survives_a_restart() -> None:
    """`hash` is salted per process; an id built from one would load a new entity every sweep."""
    first = {person.external_id for person in _roster()}
    second = {person.external_id for person in _roster()}
    assert first == second


def test_an_ensemble_is_kinded_from_the_role_the_label_filed_it_under() -> None:
    """`resolve_entity_kind` reads "NDR Radiophilharmonie" as a person — it knows
    the ``philharmon`` prefix, not a compound ending in ``-philharmonie``. The
    page says "Orchestra" outright, which is better evidence than the name."""
    orchestra = next(person for person in _roster() if person.name == "NDR Radiophilharmonie")
    assert orchestra.kind == "ensemble"
    assert orchestra.profession is None


def test_a_chorus_master_is_not_an_ensemble() -> None:
    """The ensemble roles are matched exactly, so the role of a person survives."""
    master = credits(ALBUM_HTML.replace(">Orchestra<", ">Chorus master<"))[0]
    assert master.is_ensemble is False


def test_an_ensemble_named_as_one_is_still_an_ensemble_without_a_role() -> None:
    quartet = credits(
        ALBUM_HTML.replace("NDR\n          Radiophilharmonie", "Quatuor Ebene").replace(">Orchestra<", "><")
    )[0]
    assert quartet.is_ensemble is True


def test_a_composers_column_credit_becomes_a_composer() -> None:
    prokofiev = next(person for person in _roster() if person.name == "Sergey Prokofiev")
    assert prokofiev.profession == "composer"


def test_composer_beats_a_performing_role_and_conductor_beats_soloist() -> None:
    roster = _roster()
    by_name = {person.name: person for person in roster}
    assert by_name["Stanislav Kochanovsky"].profession == "conductor"
    assert by_name["Nobuyuki Tsujii"].profession == "soloist"
    composer = by_name["Sergey Prokofiev"]
    composer.roles.add("Piano")
    assert composer.profession == "composer"
    assert composer.disciplines == ["Piano"]


def test_a_bare_name_folds_into_the_linked_person_of_the_same_name() -> None:
    """The same person is linked on one album and named without a link on another."""
    bare = ALBUM_HTML.replace(
        '<a href="https://www.harmoniamundi.com/en/artistes/stanislav-kochanovsky/">Stanislav\n'
        '          Kochanovsky <i class="fal fa-external-link"></i></a>',
        "Stanislav Kochanovsky",
    )
    roster = Roster()
    for credit in credits(ALBUM_HTML):
        roster.add(credit)
    for credit in credits(bare):
        roster.add(credit)
    assert roster.reconcile() == 1
    kochanovsky = [person for person in roster if person.name == "Stanislav Kochanovsky"]
    assert len(kochanovsky) == 1
    assert kochanovsky[0].external_id == "/artistes/stanislav-kochanovsky"


def test_a_profile_page_enriches_the_person_the_credits_produced() -> None:
    roster = _roster()
    profile = parse_profile(ARTIST_HTML, "artist", "stanislav-kochanovsky")
    assert profile is not None
    roster.enrich(profile)
    person = next(p for p in roster if p.name == "Stanislav Kochanovsky")
    assert person.disciplines == ["Flute"]
    assert person.bio is not None


def test_a_profile_with_no_credit_in_this_sweep_is_added_outright() -> None:
    """A capped run reads a slice of the catalogue but the whole profile set."""
    roster = _roster()
    profile = parse_profile(COMPOSER_HTML, "composer", "francois-couperin")
    assert profile is not None
    roster.enrich(profile)
    couperin = next(p for p in roster if p.name == "François Couperin")
    assert couperin.profession == "composer"
    assert (couperin.born, couperin.died) == ("1668", "1733")


def test_life_dates_from_a_tracklist_heading_reach_an_unlinked_composer() -> None:
    roster = _roster()
    roster.life_dates({"sergey prokofiev": ("1891", "1953")})
    prokofiev = next(person for person in roster if person.name == "Sergey Prokofiev")
    assert (prokofiev.born, prokofiev.died) == ("1891", "1953")
