"""Tests for the laphil page parsers.

The HTML constants are trimmed from real laphil.com pages, keeping the markup
quirks each test names and dropping the ~400KB of navigation chrome every page
otherwise carries.
"""

from __future__ import annotations

from composer_scrapers.laphil.events import artist_credits, composer_slugs, program_items
from composer_scrapers.laphil.people import parse_person
from composer_scrapers.laphil.sitemap import seed_urls
from composer_scrapers.laphil.urls import canonical, links, section, slug

# A programme with all four shapes that occur: a linked composer whose work is
# also linked; a linked composer with an unlinked work and a premiere note; an
# *unlinked* composer (a bare <span>, no page behind the name); and a
# structural row ("Intermission") that uses the same block but credits nobody.
EVENT_HTML = """
<section class="element program-block" id="program-block">
  <div class="program-block__list">
    <div class="program-section">
      <div class="program-item ">
        <h4 class="program-item__header">
          <a href="/people/manuel-de-falla"
             class="program-item__composer program-item__composer--link">FALLA</a>
        </h4>
        <div class="program-item__body">
          <div class="program-item__piece">
            <a href="/works/ritual-fire-dance" class="program-item__title program-item__title--link">
              <em>Ritual Fire Dance</em>
            </a>
          </div>
        </div>
        <div class="program-item__duration">
          c. 5 minutes
        </div>
      </div>
      <div class="program-item ">
        <h4 class="program-item__header">
          <a href="/people/beau-chiasson"
             class="program-item__composer program-item__composer--link">Beauregard CHIASSON</a>
        </h4>
        <div class="program-item__body">
          <div class="program-item__piece">
            <span class="program-item__title"><em>On Becoming</em></span>
          </div>
          <div class="program-item__underwriting">
            <p>world premiere</p>
          </div>
        </div>
      </div>
      <div class="program-item ">
        <h4 class="program-item__header">
          <span class="program-item__composer">Brian BALMAGES</span>
        </h4>
        <div class="program-item__body">
          <div class="program-item__piece">
            <span class="program-item__title"><em>Opening Night</em></span>
          </div>
        </div>
      </div>
      <div class="program-item program-item--intermission">
        <div class="program-item__body">Intermission</div>
      </div>
    </div>
  </div>
</section>
<section class="element artist-list" id="artists">
  <div class="artist-list__people">
    <div class="artist-list__person">
      <a href="/people/gustavo-dudamel" class="artist-item artist-item--linked">
        <div class="artist-item__body"><div class="artist-item__header">
          <h3 class="artist-item__title">
            Gustavo Dudamel
          </h3>
          <p class="artist-item__role">
            conductor
          </p>
        </div></div>
      </a>
    </div>
    <div class="artist-list__person">
      <div class="artist-item">
        <div class="artist-item__body"><div class="artist-item__header">
          <h3 class="artist-item__title">Jhoanna Sierralta</h3>
          <p class="artist-item__role">assistant conductor</p>
        </div></div>
      </div>
    </div>
  </div>
</section>
"""

# Brahms: JSON-LD with every field populated, and the "Born:/Died:" preamble the
# curated composer bios open with, followed by a curly-quoted pull quote.
BRAHMS_HTML = """
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@id":"https://www.laphil.com/people/johannes-brahms#person",
"@type":"Person","name":"Johannes Brahms","givenName":"Johannes","familyName":"Brahms",
"jobTitle":"composer","image":"https://images.baskercdn.com/laphil/media/brahms.png",
"url":"https://www.laphil.com/people/johannes-brahms"}]}
</script>
<header class="artist-header">
  <h1 class="artist-header__title">Johannes Brahms</h1>
  <p class="artist-header__role">composer</p>
  <favorite-artist data-component :id="'69b1f262077894f2a4c24a79'" v-slot="vm"></favorite-artist>
</header>
<div class="element text-content artist-bio">
  <p>Born: 1833, Hamburg, Germany</p>
  <p>Died: 1897, Vienna, Austria</p>
  <p>&ldquo;It is not hard to compose.&rdquo;</p>
</div>
<div class="event-grid">
  <a href="https://www.laphil.com/events/brahms-4" class="event-item__media"></a>
  <a href="/events/brahms-4"><h3 class="event-item__title">Brahms 4</h3></a>
  <a href="/events/a-german-requiem?ref=grid"><h3 class="event-item__title">A German Requiem</h3></a>
</div>
"""

# Boulez: born but not died. Dudamel: a jobTitle that is a list of honorific
# titles joined with <br>, and no bio preamble at all.
BOULEZ_HTML = """
<script type="application/ld+json">
{"@graph":[{"@type":"Person","name":"Pierre Boulez","givenName":"Pierre","familyName":"Boulez",
"jobTitle":"composer"}]}
</script>
<div class="element text-content artist-bio">Born: 1925, Montbrison, France "A radical."</div>
"""

DUDAMEL_HTML = """
<script type="application/ld+json">
{"@graph":[{"@type":"Person","name":"Gustavo Dudamel",
"jobTitle":"Artistic Director of the LA Phil <br> <br> Founding Director of YOLA"}]}
</script>
<h1 class="artist-header__title">Gustavo Dudamel</h1>
"""

# Raff: a real composer whose page says nothing at all — no JSON-LD jobTitle, no
# role, no bio. The overwhelmingly common shape, and why programme credits and
# not this page decide who is a composer.
RAFF_HTML = """
<script type="application/ld+json">
{"@graph":[{"@type":"Person","name":"Joachim Raff","givenName":"Joachim","familyName":"Raff"}]}
</script>
<h1 class="artist-header__title">Joachim Raff</h1>
"""


def test_program_items_read_composer_work_duration_and_note() -> None:
    (falla, chiasson, balmages) = program_items(EVENT_HTML)
    assert (falla.composer_slug, falla.composer_display) == ("manuel-de-falla", "FALLA")
    assert (falla.work_slug, falla.work_title) == ("ritual-fire-dance", "Ritual Fire Dance")
    assert falla.duration == "c. 5 minutes"
    assert chiasson.work_slug is None and chiasson.note == "world premiere"
    assert balmages.composer_slug is None and balmages.composer_display == "Brian BALMAGES"


def test_program_items_skip_rows_crediting_nobody() -> None:
    """ "Intermission" reuses the program-item block without a composer."""
    assert all(item.composer_display for item in program_items(EVENT_HTML))
    assert "Intermission" not in [item.work_title for item in program_items(EVENT_HTML)]


def test_composer_slugs_keeps_only_linked_credits() -> None:
    assert composer_slugs(EVENT_HTML) == {
        "manuel-de-falla": "FALLA",
        "beau-chiasson": "Beauregard CHIASSON",
    }


def test_composer_slugs_is_empty_without_a_program_block() -> None:
    assert composer_slugs("<html><body>no programme here</body></html>") == {}


def test_artist_credits_read_linked_and_unlinked_performers() -> None:
    (dudamel, sierralta) = artist_credits(EVENT_HTML)
    assert (dudamel.slug, dudamel.name, dudamel.role) == ("gustavo-dudamel", "Gustavo Dudamel", "conductor")
    assert (sierralta.slug, sierralta.role) == (None, "assistant conductor")


def test_parse_person_reads_jsonld_and_the_bio_preamble() -> None:
    person = parse_person("johannes-brahms", BRAHMS_HTML)
    assert person is not None
    assert (person.name, person.given_name, person.family_name) == ("Johannes Brahms", "Johannes", "Brahms")
    assert (person.born_year, person.born_place) == ("1833", "Hamburg, Germany")
    assert (person.died_year, person.died_place) == ("1897", "Vienna, Austria")
    assert person.job_title == "composer" and person.declares_composer
    assert person.artist_id == "69b1f262077894f2a4c24a79"
    assert person.url == "https://www.laphil.com/people/johannes-brahms"


def test_parse_person_handles_a_birth_year_with_no_death() -> None:
    person = parse_person("pierre-boulez", BOULEZ_HTML)
    assert person is not None
    assert (person.born_year, person.born_place) == ("1925", "Montbrison, France")
    assert (person.died_year, person.died_place) == (None, None)


def test_parse_person_flattens_a_multi_line_job_title() -> None:
    person = parse_person("gustavo-dudamel", DUDAMEL_HTML)
    assert person is not None
    assert person.job_title == "Artistic Director of the LA Phil Founding Director of YOLA"
    assert not person.declares_composer


def test_parse_person_accepts_a_page_that_declares_nothing() -> None:
    person = parse_person("joachim-raff", RAFF_HTML)
    assert person is not None
    assert person.name == "Joachim Raff"
    assert (person.job_title, person.born_year, person.declares_composer) == ("", None, False)


def test_parse_person_falls_back_to_the_header_when_jsonld_is_unreadable() -> None:
    html = '<script type="application/ld+json">{not json</script>' + RAFF_HTML.split("</script>", 1)[1]
    person = parse_person("joachim-raff", html)
    assert person is not None and person.name == "Joachim Raff"


def test_parse_person_returns_none_without_a_name() -> None:
    assert parse_person("nobody", "<html><body></body></html>") is None


def test_person_event_urls_are_canonical_and_deduplicated() -> None:
    person = parse_person("johannes-brahms", BRAHMS_HTML)
    assert person is not None
    assert person.event_urls == (
        "https://www.laphil.com/events/brahms-4",
        "https://www.laphil.com/events/a-german-requiem",
    )


def test_canonical_normalises_every_spelling_of_a_page() -> None:
    expected = "https://www.laphil.com/people/x"
    for href in (
        "/people/x",
        "/people/x/",
        "https://laphil.com/people/x",
        "https://www.laphil.com/people/x#bio",
    ):
        assert canonical(href) == expected


def test_canonical_rejects_pages_this_source_does_not_read() -> None:
    """Off-site links, other sections, and the per-performance event permalink
    (already reached by its /events/<slug> form)."""
    rejected = (
        "/posts/q",
        "/works/w",
        "https://example.com/people/n",
        "/events/instances/ab/2026-01-01/w",
        "",
    )
    for href in rejected:
        assert canonical(href) is None


def test_links_collects_both_sections_in_document_order() -> None:
    assert links(EVENT_HTML) == [
        "https://www.laphil.com/people/manuel-de-falla",
        "https://www.laphil.com/people/beau-chiasson",
        "https://www.laphil.com/people/gustavo-dudamel",
    ]


def test_section_and_slug_split_a_canonical_url() -> None:
    url = "https://www.laphil.com/events/brahms-4"
    assert (section(url), slug(url)) == ("events", "brahms-4")


def test_seed_urls_keeps_events_and_people_in_file_order() -> None:
    sitemap = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.laphil.com/events/brahms-4</loc></url>
      <url><loc>https://www.laphil.com/posts/an-article</loc></url>
      <url><loc>https://www.laphil.com/people/johannes-brahms</loc></url>
      <url><loc>https://www.laphil.com/events/brahms-4</loc></url>
    </urlset>"""
    assert seed_urls(sitemap) == [
        "https://www.laphil.com/events/brahms-4",
        "https://www.laphil.com/people/johannes-brahms",
    ]
