"""Tests for IMSLP work detail page parsing.

The HTML fragments below mirror the real markup shape confirmed live against
imslp.org/wiki/Piano_Sonata_No.32,_Op.111_(Beethoven,_Ludwig_van) while
building this source: a ``<tr><th>Label</th><td>Value</td></tr>`` infobox,
some labels wrapping a long/short form in ``mh555``/``ms555`` spans, and a
later "Sheet Music" table reusing unrelated ``<th>`` labels per uploaded file.
"""

from __future__ import annotations

from composer_scrapers.imslp_works.works import parse_work, strip_composer_suffix

WORK_PAGE = """
<html>
<head><title>Piano Sonata No.32, Op.111 (Beethoven, Ludwig van) - IMSLP</title></head>
<body>
<h1 id="firstHeading" class="firstHeading pagetitle page-header">Piano Sonata No.32, Op.111
(Beethoven, Ludwig van)</h1>
<table>
<tr><th>Composer</th><td><a href="/wiki/Category:Beethoven,_Ludwig_van">Beethoven, Ludwig van</a></td></tr>
<tr><th><span class="mh555">Opus/Catalogue Number</span><span class="ms555">Op./Cat. No.</span></th>
<td>Op.111</td></tr>
<tr><th><span class="mh555">Internal Reference Number</span><span class="ms555">Internal Ref. No.</span></th>
<td>ILB 193</td></tr>
<tr><th>Key</th><td>C minor</td></tr>
<tr><th><span class="mh555">Movements/Sections</span><span class="ms555">Mov'ts/Sec's</span></th>
<td>2 movements:
<ol><li>Maestoso - Allegro con brio ed appassionato</li><li>Arietta. Adagio molto semplice cantabile</li></ol>
</td></tr>
<tr><th><span class="mh555">Year/Date of Composition</span><span class="ms555">Y/D of Comp.</span></th>
<td>1821&#8211;22</td></tr>
<tr><th><span class="mh555">Composer Time Period</span><span class="ms555">Comp. Period</span></th>
<td><a href="/wiki/Category:Classical">Classical</a></td></tr>
<tr><th>Piece Style</th><td><a href="/wiki/Category:Romantic_style">Romantic</a></td></tr>
<tr><th>Genre Categories<span class="addpagetag mh555"></span></th>
<td><a href="...">Sonatas</a>; <a href="...">For piano</a></td></tr>
<tr><th>Instrumentation</th><td>piano</td></tr>
<tr><th>Discography</th><td><a href="https://musicbrainz.org/work/x">MusicBrainz</a></td></tr>
</table>
<h2>Sheet Music</h2>
<table>
<tr><th>Performer Pages</th><td>Peter Bradley-Fulgoni (piano)</td></tr>
<tr><th>Publisher Info.</th><td>Peter Bradley-Fulgoni</td></tr>
<tr><th>Copyright</th><td>Creative Commons Attribution-NonCommercial-NoDerivs 4.0</td></tr>
</table>
</body>
</html>
"""

WORK_PAGE_SPARSE = """
<html>
<head><title>Fragment (Anonymous) - IMSLP</title></head>
<body><table><tr><th>Composer</th><td>Anonymous</td></tr></table></body>
</html>
"""


def test_parse_work_reads_the_title_from_the_title_tag_and_strips_the_site_suffix() -> None:
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert work.title == "Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)"


def test_parse_work_reads_instrumentation() -> None:
    """The field the user actually asked for."""
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert work.fields["instrumentation"] == "piano"


def test_parse_work_reads_the_long_form_label_dropping_the_short_form() -> None:
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert work.fields["opus_catalogue_number"] == "Op.111"
    assert work.fields["internal_reference_number"] == "ILB 193"
    assert work.fields["composition_year"] == "1821–22"


def test_parse_work_reads_key_and_genre_categories() -> None:
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert work.fields["key"] == "C minor"
    assert work.fields["genre_categories"] == "Sonatas ; For piano"


def test_parse_work_does_not_bleed_into_the_sheet_music_table() -> None:
    """ "Publisher Info." / "Copyright" belong to the per-file scores table,
    not the infobox, and must not overwrite/appear as recognised fields."""
    work = parse_work(WORK_PAGE)
    assert work is not None
    assert "publisher_info" not in work.fields
    assert "copyright" not in work.fields


def test_parse_work_omits_fields_the_page_does_not_state() -> None:
    work = parse_work(WORK_PAGE_SPARSE)
    assert work is not None
    assert "instrumentation" not in work.fields
    assert "key" not in work.fields


def test_parse_work_returns_none_without_a_title() -> None:
    assert parse_work("<html><body><table></table></body></html>") is None


def test_strip_composer_suffix_removes_the_trailing_composer_qualifier() -> None:
    title = "Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)"
    assert strip_composer_suffix(title, "Beethoven, Ludwig van") == "Piano Sonata No.32, Op.111"


def test_strip_composer_suffix_leaves_a_title_without_the_suffix_untouched() -> None:
    assert strip_composer_suffix("Kerori", "Walter Steffens") == "Kerori"
