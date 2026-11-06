"""Tests for the Classicapedia parsers. The HTML is cut down from real pages."""

from __future__ import annotations

from composer_scrapers.classicapedia.fetch import page_refs, parse_index
from composer_scrapers.classicapedia.people import Relation, parse_biography, plain_name
from composer_scrapers.classicapedia.resolve import (
    WikidataItem,
    confirms_namesake,
    confirms_person,
    parse_item,
)
from composer_scrapers.classicapedia.venues import parse_profile
from composer_scrapers.classicapedia.works import parse_works, strip_key

BEETHOVEN = """
<article class="ficha p-clasicismo"><header><h1>Ludwig van Beethoven</h1>
<p class="sub">1770–1827 · Bonn · German composer and pianist</p></header>
<div class="texto"><div id="bio" data-ind="Biography"><p>Ludwig van Beethoven (Bonn, 1770).</p></div>
<h2 id="obras" data-ind="Notable works">Notable works</h2><h3>Orchestral</h3><ul class="obras">
<li>Symphony no.9, d <span class="anyo">(1822–4)</span></li></ul>
<section id="catalogo" data-ind="Catalogue of works"><h2>Catalogue of works</h2>
<details><summary>Orchestral <span class="num">(2)</span></summary><ul class="obras">
<li>Symphony no.3 Eroica, E <span class="anyo">(1803)</span></li>
<li>Fantasia, c, pf, chorus, orch (Choral Fantasy) <span class="anyo">(1808)</span></li>
</ul></details><details><summary>Songs <span class="num">(1)</span></summary><ul class="obras">
<li>An Minna, woo 115 <span class="anyo">(c1792)</span></li></ul></details></section>
<section id="maestros" data-ind="Teachers and pupils"><h2>Teachers and pupils</h2>
<h3>Studied with</h3><ul class="obras rel">
<li><a href="joseph-haydn.html">Joseph Haydn</a> <span class="mat">(counterpoint)</span></li>
<li><a href="antonio-salieri.html">Antonio Salieri</a></li>
<li>Wenzel Krumpholz <span class="mat">(violin)</span></li></ul>
<h3>Pupils</h3><ul class="obras rel">
<li><a href="carl-czerny.html">Carl Czerny</a> <span class="mat">(piano)</span></li>
<li><a href="ignace-pleyel.html">Ignace Joseph Pleyel</a> <span class="mat">(i)</span></li>
<li><a href="anton-kraft.html">Anton Kraft and</a></li></ul></section>
<section id="curiosidades"><p class="fuente-wiki">Source:
<a href="https://es.wikipedia.org/wiki/Ludwig_van_Beethoven">Wikipedia (ES)</a> ·
<a href="https://creativecommons.org/licenses/by-sa/4.0/deed.en">CC BY-SA 4.0</a></p></section>
</div></article>
"""


def _bio(subtitle: str, extra: str = "") -> str:
    return f'<article class="ficha"><h1>Somebody</h1><p class="sub">{subtitle}</p>{extra}</article>'


def test_a_biography_states_its_years_birthplace_and_occupation() -> None:
    bio = parse_biography("ludwig-van-beethoven", BEETHOVEN)
    assert bio is not None
    assert (bio.name, bio.birth_year, bio.death_year) == ("Ludwig van Beethoven", "1770", "1827")
    assert bio.birth_place == "Bonn"
    assert bio.occupation == "German composer and pianist"


def test_teachers_and_pupils_keep_the_link_and_the_subject() -> None:
    bio = parse_biography("ludwig-van-beethoven", BEETHOVEN)
    assert bio is not None
    assert bio.teachers == (
        Relation("Joseph Haydn", "joseph-haydn", "counterpoint"),
        Relation("Antonio Salieri", "antonio-salieri"),
        Relation("Wenzel Krumpholz", None, "violin"),
    )


def test_list_noise_is_removed_from_names_and_subjects() -> None:
    """A dictionary's "(i)" is not a subject, and "and" is not part of a name."""
    bio = parse_biography("ludwig-van-beethoven", BEETHOVEN)
    assert bio is not None
    assert bio.pupils[1] == Relation("Ignace Joseph Pleyel", "ignace-pleyel")
    assert bio.pupils[2] == Relation("Anton Kraft", "anton-kraft")
    assert plain_name("Ignace Joseph Pleyel (ii)") == "Ignace Joseph Pleyel"
    assert plain_name("Louis XIV") == "Louis XIV"


def test_a_link_outside_the_section_is_not_a_teacher() -> None:
    page = _bio("1800–1850 · Paris · composer", '<ul class="obras rel"><li><a href="x.html">X</a></li></ul>')
    bio = parse_biography("somebody", page)
    assert bio is not None
    assert bio.teachers == ()


def test_the_only_cited_article_is_kept_and_two_say_nothing() -> None:
    bio = parse_biography("ludwig-van-beethoven", BEETHOVEN)
    assert bio is not None
    assert bio.wikipedia == ("es", "Ludwig van Beethoven")
    two = (
        '<p class="fuente-wiki"><a href="https://es.wikipedia.org/wiki/A">a</a></p>'
        '<p class="fuente-wiki"><a href="https://es.wikipedia.org/wiki/B">b</a></p>'
    )
    other = parse_biography("somebody", _bio("1800–1850 · Paris · composer", two))
    assert other is not None
    assert other.wikipedia is None


def test_date_shapes() -> None:
    for subtitle, years in (
        ("b. 1966 · Barva · composer", ("1966", None)),
        ("d. 1664 · London · singer", (None, "1664")),
        ("fl. c. 1500 · Rome · composer", (None, None)),
        ("999–1050 · Arezzo · theorist", ("999", "1050")),
    ):
        bio = parse_biography("somebody", _bio(subtitle))
        assert bio is not None
        assert (bio.birth_year, bio.death_year) == years


def test_a_span_that_cannot_be_a_life_states_no_years_but_stays_in_dates() -> None:
    for dates in ("1904–1904", "384–322", "1585–163", "1846–1996", "172–1790"):
        bio = parse_biography("somebody", _bio(f"{dates} · Rome · composer"))
        assert bio is not None
        assert (bio.birth_year, bio.death_year) == (None, None)
        assert bio.dates == dates


def test_a_long_life_is_still_a_life() -> None:
    bio = parse_biography("somebody", _bio("1695–1807 · Newtown · harpist"))
    assert bio is not None
    assert (bio.birth_year, bio.death_year) == ("1695", "1807")


def test_the_middle_slot_is_believed_only_when_it_is_one_place() -> None:
    for slot, place in (
        ("Rohrau", "Rohrau"),
        ("Gebweiler [now Guebwiller]", "Gebweiler"),
        ("Milan or Vercelli", None),
        ("c 1650", None),
        ("composer of songs", None),
    ):
        bio = parse_biography("somebody", _bio(f"1700–1750 · {slot} · composer"))
        assert bio is not None
        assert bio.birth_place == place


def test_a_slot_with_a_circa_date_withdraws_the_birth_year_only() -> None:
    bio = parse_biography("somebody", _bio("1650–1702 · c 1650 · German composer"))
    assert bio is not None
    assert (bio.birth_year, bio.death_year) == (None, "1702")


def test_a_subtitle_without_a_place_slot_still_gives_the_occupation() -> None:
    bio = parse_biography("somebody", _bio("1793–1873 · conductor and composer"))
    assert bio is not None
    assert (bio.birth_place, bio.occupation) == (None, "conductor and composer")


def test_a_family_page_is_recognised() -> None:
    family = parse_biography("haas", _bio("1689–1752 · Hamburg · family of German harpsichord makers"))
    person = parse_biography("somebody", _bio("1700–1750 · Bonn · composer of a family of songs"))
    assert family is not None
    assert person is not None
    assert family.is_family
    assert not person.is_family


def test_a_page_without_a_name_is_nothing() -> None:
    assert parse_biography("x", '<article class="ficha"></article>') is None


def test_the_catalogue_wins_over_the_notable_list() -> None:
    works = parse_works(BEETHOVEN)
    assert [work.title for work in works] == [
        "Symphony no.3 Eroica",
        "Fantasia, pf, chorus, orch (Choral Fantasy)",
        "An Minna, woo 115",
    ]
    assert [work.genre for work in works] == ["Orchestral", "Orchestral", "Songs"]


def test_a_key_letter_leaves_the_title_but_stays_in_raw() -> None:
    """The site loses accidentals: the Eroica's "E" is E flat."""
    eroica = parse_works(BEETHOVEN)[0]
    assert eroica.title == "Symphony no.3 Eroica"
    assert eroica.as_raw() == {"listed_as": "Symphony no.3 Eroica, E", "genre": "Orchestral", "years": "1803"}


def test_only_a_key_token_is_stripped() -> None:
    assert strip_key("Symphony no.5, c") == "Symphony no.5"
    assert strip_key("Der Minnesänger, D.981") == "Der Minnesänger, D.981"
    assert strip_key("Overture, D2a (frag.)") == "Overture, D2a (frag.)"


def _notable(*items: str) -> str:
    lis = "".join(f"<li>{item}</li>" for item in items)
    return f'<h2 id="obras">Notable works</h2><ul class="obras">{lis}</ul><section id="maestros"></section>'


def test_summaries_are_not_works_but_numbered_titles_are() -> None:
    page = _notable(
        "Recuerdos de la Alhambra",
        "Approximately 78 original works for guitar",
        "Some 120 transcriptions for guitar",
        "Songs for voice and piano (around 200)",
        "1812 Overture",
        "6 sonatas, vn",
    )
    assert [work.title for work in parse_works(page)] == [
        "Recuerdos de la Alhambra",
        "1812 Overture",
        "6 sonatas, vn",
    ]


def test_dates_written_into_the_title_move_to_years() -> None:
    page = _notable(
        "Vibracije (ballet, 1955)", "Premier livre de sonates, vn (Paris, 1731), lost", "Dr Doolittle (1937)"
    )
    works = parse_works(page)
    assert [(work.title, work.years) for work in works] == [
        ("Vibracije", "ballet, 1955"),
        ("Premier livre de sonates, vn", "Paris, 1731"),
        ("Dr Doolittle", "1937"),
    ]


def test_teacher_lists_are_not_mistaken_for_works() -> None:
    page = '<h2 id="obras">x</h2><ul class="obras rel"><li><a href="a.html">A</a></li></ul>'
    assert parse_works(page) == ()


HALL = """
<article class="ficha sala-ficha"><header>
<p class="periodo-tag tipo-tag">Concert hall · Vienna, Austria</p><h1>Musikverein, Vienna</h1>
<p class="sub">Vienna&#x27;s concert hall.</p></header>
<dl class="datos-sala"><dt>Opened</dt><dd>1870</dd><dt>Architect</dt><dd>Theophil Hansen</dd>
<dt>Great Hall capacity</dt><dd>1,744 seats</dd></dl>
<div class="texto"><p>See <a href="https://en.wikipedia.org/wiki/Golden_Hall">a link in the text</a>.</p>
<p class="mas-info">Further reading: <a href="https://en.wikipedia.org/wiki/Musikverein">Wikipedia</a></p></div></article>
"""


def test_a_profile_states_its_place_facts_and_article() -> None:
    profile = parse_profile("s", "musikverein-de-viena", HALL)
    assert profile is not None
    assert (profile.name, profile.city, profile.country) == ("Musikverein, Vienna", "Vienna", "Austria")
    assert profile.opened == "1870"
    assert profile.founded is None
    assert dict(profile.facts)["Architect"] == "Theophil Hansen"


def test_only_the_further_reading_link_ties_a_profile_to_wikipedia() -> None:
    profile = parse_profile("s", "musikverein-de-viena", HALL)
    assert profile is not None
    assert profile.wikipedia == ("en", "Musikverein")


def test_a_city_state_has_no_country() -> None:
    page = (
        '<article class="ficha"><p class="periodo-tag tipo-tag">Concert hall · Singapore</p>'
        "<h1>Esplanade</h1></article>"
    )
    profile = parse_profile("s", "esplanade", page)
    assert profile is not None
    assert (profile.city, profile.country) == ("Singapore", None)


def test_an_orchestra_states_its_founding() -> None:
    page = (
        '<article class="ficha"><p class="periodo-tag tipo-tag">Orchestra · Vienna, Austria</p>'
        '<h1>Vienna Philharmonic</h1><dl class="datos-sala"><dt>Founded</dt><dd>1842</dd></dl></article>'
    )
    profile = parse_profile("o", "viena", page)
    assert profile is not None
    assert profile.founded == "1842"
    assert profile.wikipedia is None


def test_the_sitemap_keeps_only_the_sections_this_source_reads() -> None:
    xml = (
        "<urlset><url><loc>https://classicapedia.com/en/maestros.html</loc></url>"
        "<url><loc>https://classicapedia.com/en/b/joseph-haydn.html</loc></url>"
        "<url><loc>https://classicapedia.com/en/p/a-story.html</loc></url>"
        "<url><loc>https://classicapedia.com/en/t/la-fenice.html</loc></url>"
        "<url><loc>https://classicapedia.com/b/joseph-haydn.html</loc></url></urlset>"
    )
    assert [(ref.section, ref.slug) for ref in page_refs(xml)] == [("b", "joseph-haydn"), ("t", "la-fenice")]


def test_the_search_index_gives_clean_names_and_skips_bad_rows() -> None:
    script = (
        'window.INDICE=[["a-b","Anna B (i)","1800–1850","romanticismo",["compositor"],"German","x","y",1],'
        '["too-short","Name"],[1,2,3,4,5,6,7]];'
    )
    entries = parse_index(script)
    assert [(entry.slug, entry.name, entry.activities) for entry in entries] == [
        ("a-b", "Anna B", ("compositor",))
    ]


def _item(*instance_of: str, born: int | None = 1770, died: int | None = 1827) -> WikidataItem:
    return WikidataItem(
        qid="Q1",
        label="Someone",
        instance_of=frozenset(instance_of),
        birth_years=frozenset({born} if born else ()),
        death_years=frozenset({died} if died else ()),
    )


def test_a_person_needs_to_be_human_with_fitting_years() -> None:
    assert confirms_person(_item("Q5"), "1770", "1827")
    assert confirms_person(_item("Q5"), "1771", "1826")  # sources disagree by a year
    assert not confirms_person(_item("Q5"), "1800", "1827")  # one year contradicts
    assert not confirms_person(_item("Q2188189"), "1770", "1827")  # a musical work


def test_no_year_to_compare_is_no_pin() -> None:
    assert not confirms_person(_item("Q5"), None, None)
    assert not confirms_person(_item("Q5", born=None, died=None), "1770", "1827")
    assert confirms_person(_item("Q5", died=None), "1770", None)


def test_a_namesake_needs_both_years_to_fit() -> None:
    assert confirms_namesake(_item("Q5"), "1771", "1826")
    assert not confirms_namesake(_item("Q5"), "1770", None)
    assert not confirms_namesake(_item("Q5", died=None), "1770", "1827")
    assert not confirms_namesake(_item("Q5"), "1770", "1850")
    assert not confirms_namesake(_item("Q3305213"), "1770", "1827")


def test_an_item_is_read_from_the_api_answer() -> None:
    item = parse_item(
        "Q255",
        {
            "labels": {"en": {"value": "Ludwig van Beethoven"}},
            "claims": {
                "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}],
                "P569": [
                    {"mainsnak": {"datavalue": {"value": {"time": "+1770-12-17T00:00:00Z"}}}},
                    {
                        "rank": "deprecated",
                        "mainsnak": {"datavalue": {"value": {"time": "+1771-01-01T00:00:00Z"}}},
                    },
                    {"mainsnak": {"snaktype": "novalue"}},
                ],
            },
        },
    )
    assert item.label == "Ludwig van Beethoven"
    assert item.instance_of == {"Q5"}
    assert item.birth_years == {1770}
    assert item.death_years == frozenset()
