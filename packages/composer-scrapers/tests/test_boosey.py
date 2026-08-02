"""Tests for the Boosey & Hawkes catalogue and work-page parsing.

The HTML constants below are hand-written stand-ins, not captures of live pages
(boosey.com is not reachable from CI). They pin the parser's *behaviour* — label
matching, fallbacks, tolerance of unknown fields — and deliberately use two
different layouts for the same data, because the parser is meant to read labels
rather than markup. Replace them with trimmed real pages once one is on hand.
"""

from __future__ import annotations

from composer_scrapers.boosey.catalogue import composer_paths, next_page_path, work_links
from composer_scrapers.boosey.works import duration_minutes, parse_work, text_lines

# A definition-list layout.
WORK_PAGE = """
<html>
<head><title>Kerori | Boosey &amp; Hawkes</title></head>
<body>
  <a href="/composers">Composers</a>
  <h1>Kerori</h1>
  <p>by <a href="/composer/Walter+Steffens">Walter Steffens</a></p>
  <dl>
    <dt>Year Composed</dt><dd>1998</dd>
    <dt>Duration</dt><dd>12'</dd>
    <dt>Scoring</dt><dd>2(II=picc).2.2.2 - 4.2.3.1 - timp - str</dd>
    <dt>Abbreviated Scoring</dt><dd>2.2.2.2-4.2.3.1-timp-str</dd>
    <dt>Publisher</dt><dd>Boosey &amp; Hawkes</dd>
    <dt>Territory</dt><dd>This work is available from Boosey &amp; Hawkes for the world.</dd>
  </dl>
</body>
</html>
"""

# The same fields in an inline "Label: value" layout, plus a label the parser
# does not know about.
WORK_PAGE_INLINE = """
<html>
<head><title>Sinfonia - Boosey &amp; Hawkes</title></head>
<body>
  <h1>Sinfonia</h1>
  <a href="/composer/Some+Composer">Some Composer</a>
  <p><strong>Duration:</strong> c. 24 minutes</p>
  <p><strong>Scoring:</strong> 3.3.3.3 - 4.3.3.1 - perc - str</p>
  <p><strong>Catalogue Reference:</strong> BH 12345</p>
</body>
</html>
"""

# A work with neither duration nor scoring stated.
WORK_PAGE_SPARSE = """
<html>
<head><title>Fragment | Boosey &amp; Hawkes</title></head>
<body>
  <h1>Fragment</h1>
  <a href="/composer/Anon">Anon</a>
  <dl><dt>Year Composed</dt><dd>1911</dd></dl>
</body>
</html>
"""

WORK_LIST = """
<ul>
  <li><a href="/cr/music/Walter-Steffens-Kerori/27637">Kerori</a>
      <a href="/cr/music/Walter-Steffens-Kerori/27637"><img src="thumb.jpg"></a></li>
  <li><a href="https://www.boosey.com/cr/music/Walter-Steffens-Eichendorff/12345">Eichendorff</a></li>
  <li><a href="/cr/perusals/something/999">Perusal score</a></li>
</ul>
<a rel="next" href="/composer/Walter+Steffens?page=2">Next</a>
"""


# ---------------------------------------------------------------------------
# catalogue: link discovery
# ---------------------------------------------------------------------------


def test_work_links_keys_on_the_work_url_shape() -> None:
    links = work_links(WORK_LIST)
    assert [(link.work_id, link.path) for link in links] == [
        ("27637", "/cr/music/Walter-Steffens-Kerori/27637"),
        ("12345", "/cr/music/Walter-Steffens-Eichendorff/12345"),
    ]


def test_work_links_ignores_non_work_catalogue_urls() -> None:
    assert work_links('<a href="/cr/perusals/x/999">Perusal</a>') == []


def test_work_links_are_deduplicated_by_id() -> None:
    html = '<a href="/cr/music/a-b/1">x</a><a href="/cr/music/a-b-retitled/1">x</a>'
    assert [link.work_id for link in work_links(html)] == ["1"]


def test_composer_paths_dedupe_and_keep_order() -> None:
    html = """
    <a href="/composer/Aaron+Copland">Copland</a>
    <a href="/composer/Aaron+Copland">Copland again</a>
    <a href="https://www.boosey.com/composer/Benjamin+Britten">Britten</a>
    <a href="/cr/music/x/1">not a composer</a>
    """
    assert composer_paths(html) == ["/composer/Aaron+Copland", "/composer/Benjamin+Britten"]


def test_next_page_path_reads_rel_next_either_attribute_order() -> None:
    assert next_page_path(WORK_LIST) == "/composer/Walter+Steffens?page=2"
    assert next_page_path('<a href="/p/2" rel="next">Next</a>') == "/p/2"


def test_next_page_path_is_none_on_the_last_page() -> None:
    assert next_page_path('<a href="/p/1">1</a>') is None


# ---------------------------------------------------------------------------
# works: page parsing
# ---------------------------------------------------------------------------


def test_text_lines_flattens_blocks_and_drops_scripts() -> None:
    html = "<script>var x = '<p>no</p>';</script><p>one</p><div>two</div>"
    assert text_lines(html) == ["one", "two"]


def test_parse_work_reads_a_definition_list_layout() -> None:
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert work.title == "Kerori"
    assert work.composer == "Walter Steffens"
    assert work.fields["year"] == "1998"
    assert work.fields["duration"] == "12'"
    assert work.fields["scoring"] == "2(II=picc).2.2.2 - 4.2.3.1 - timp - str"
    assert work.fields["abbreviated_scoring"] == "2.2.2.2-4.2.3.1-timp-str"
    assert work.fields["publisher"] == "Boosey & Hawkes"


def test_parse_work_reads_an_inline_label_layout() -> None:
    work = parse_work(WORK_PAGE_INLINE)
    assert work is not None
    assert work.title == "Sinfonia"
    assert work.composer == "Some Composer"
    assert work.fields["duration"] == "c. 24 minutes"
    assert work.fields["scoring"] == "3.3.3.3 - 4.3.3.1 - perc - str"


def test_parse_work_ignores_unknown_labels() -> None:
    work = parse_work(WORK_PAGE_INLINE)
    assert work is not None
    assert "BH 12345" not in work.fields.values()


def test_abbreviated_scoring_does_not_swallow_scoring() -> None:
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert work.fields["scoring"] != work.fields["abbreviated_scoring"]


def test_parse_work_omits_fields_the_page_does_not_state() -> None:
    work = parse_work(WORK_PAGE_SPARSE)
    assert work is not None
    assert work.title == "Fragment"
    assert "duration" not in work.fields
    assert "scoring" not in work.fields
    assert work.fields["year"] == "1911"


def test_parse_work_ignores_the_composer_index_link() -> None:
    """The nav's "Composers" link must not be mistaken for the composer."""
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert work.composer == "Walter Steffens"


def test_parse_work_falls_back_to_the_title_tag_without_an_h1() -> None:
    work = parse_work("<html><head><title>Nocturne | Boosey &amp; Hawkes</title></head><body></body></html>")
    assert work is not None
    assert work.title == "Nocturne"


def test_parse_work_returns_none_without_a_title() -> None:
    assert parse_work("<html><body></body></html>") is None


# ---------------------------------------------------------------------------
# works: duration normalisation
# ---------------------------------------------------------------------------


def test_duration_minutes_reads_the_prime_notation() -> None:
    assert duration_minutes("12'") == 12


def test_duration_minutes_ignores_a_circa_prefix() -> None:
    assert duration_minutes("c. 12 minutes") == 12


def test_duration_minutes_takes_the_lower_bound_of_a_range() -> None:
    assert duration_minutes("12-14 mins") == 12


def test_duration_minutes_adds_hours_and_minutes() -> None:
    assert duration_minutes("1 hour 5 minutes") == 65
    assert duration_minutes("2 hrs") == 120


def test_duration_minutes_is_none_when_absent_or_unparseable() -> None:
    assert duration_minutes(None) is None
    assert duration_minutes("") is None
    assert duration_minutes("variable") is None
