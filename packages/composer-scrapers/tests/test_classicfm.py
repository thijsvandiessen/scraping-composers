"""Tests for classicfm.com's composer/artist index parser."""

from __future__ import annotations

from composer_scrapers.classicfm.parse import parse_entries

# Trimmed excerpts of the real rendered HTML from classicfm.com/composers/ and
# classicfm.com/artists/, kept close to verbatim so the regex is exercised
# against markup quirks that matter: incidental whitespace around names, HTML
# entities, and the separate "Featured composers"/"Featured artists" card
# carousel that repeats a few of the same names without the
# grouped_links__list__link class.

COMPOSERS_PAGE = """
<div class="cardset level3">
    <h2 class="cardset__title"><span tabindex="0">Featured composers</span></h2>
    <div class="editorial editorial_card promo short-form portrait">
        <a href="/composers/bach/">
            <h3>Johann Sebastian Bach</h3>
        </a>
    </div>
</div>
<div class="grouped_links">
    <div class="grouped_links__groups">
        <div class="grouped_links__group" id="grouped_links62232_A">
            <h3>A</h3>
            <ul class="grouped_links__list">
                <li><a href="/composers/A-scarlatti/" class="grouped_links__list__link">A Scarlatti</a></li>
                <li><a href="/composers/addinsell/" class="grouped_links__list__link">Addinsell</a></li>
                <li><a href="/composers/ades/" class="grouped_links__list__link">Ad&egrave;s</a></li>
                <li><a href="/composers/arnold-d/" class="grouped_links__list__link">Arnold, D</a></li>
            </ul>
        </div>
        <div class="grouped_links__group" id="grouped_links62232_B">
            <h3>B</h3>
            <ul class="grouped_links__list">
                <li><a href="/composers/bach/" class="grouped_links__list__link">Bach</a></li>
                <li><a href="/composers/bernstein-l/" class="grouped_links__list__link">Bernstein, L</a></li>
            </ul>
        </div>
    </div>
</div>
"""

ARTISTS_PAGE = """
<div class="grouped_links">
    <div class="grouped_links__groups">
        <div class="grouped_links__group" id="grouped_links61753_C">
            <h3>C</h3>
            <ul class="grouped_links__list">
                <li>
                    <a href="/artists/camille-julie/" class="grouped_links__list__link">
                        Camille &amp; Julie
                    </a>
                </li>
                <li><a href="/artists/craig-ogden/" class="grouped_links__list__link">Craig Ogden  </a></li>
            </ul>
        </div>
    </div>
</div>
"""


def test_parses_all_names_and_paths() -> None:
    entries = parse_entries(COMPOSERS_PAGE)
    assert [(e.path, e.name) for e in entries] == [
        ("/composers/A-scarlatti/", "A Scarlatti"),
        ("/composers/addinsell/", "Addinsell"),
        ("/composers/ades/", "Adès"),
        ("/composers/arnold-d/", "Arnold, D"),
        ("/composers/bach/", "Bach"),
        ("/composers/bernstein-l/", "Bernstein, L"),
    ]


def test_skips_featured_card_duplicates() -> None:
    """Bach appears in the featured carousel (its <a> carries no class) and
    in the grouped_links index (with the class) — only the latter is found,
    and only once."""
    entries = parse_entries(COMPOSERS_PAGE)
    bach = [e for e in entries if e.path == "/composers/bach/"]
    assert len(bach) == 1
    assert bach[0].name == "Bach"


def test_unescapes_html_entities_in_names() -> None:
    entries = parse_entries(ARTISTS_PAGE)
    assert entries[0].name == "Camille & Julie"


def test_strips_incidental_whitespace_from_names() -> None:
    entries = parse_entries(ARTISTS_PAGE)
    assert entries[1].name == "Craig Ogden"


def test_dedupes_by_path_preserving_order() -> None:
    page_html = """
    <div class="grouped_links__groups">
        <div class="grouped_links__group">
            <ul class="grouped_links__list">
                <li><a href="/composers/bach/" class="grouped_links__list__link">Bach</a></li>
                <li><a href="/composers/bach/" class="grouped_links__list__link">Bach</a></li>
            </ul>
        </div>
    </div>
    """
    entries = parse_entries(page_html)
    assert len(entries) == 1


def test_no_matches_returns_empty_list() -> None:
    assert parse_entries("<html><body>nothing here</body></html>") == []
