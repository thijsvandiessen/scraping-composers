"""Tests for parsing the Concertgebouw archive search page dropdowns."""

from composer_ingest.sources import SourceClaim
from composer_ingest.sources.concertgebouw.dropdowns import _options, _record
from composer_ingest.sources.concertgebouw.performances import _performances
from doc_helpers import MentionView, RecordView

# Trimmed copy of the real page structure: unquoted option values, a quoted
# "0" placeholder, HTML entities, and the label formats of all three selects.
PAGE = """\
<form id="zoeken" method="post">
<select id="dirigentcode" name="dirigentcode" class="select2 s2default">
    <option class="placeholder" value="0" selected="selected">&lt;geen selectie&gt;</option>
    <option value=2182>Abbado, Claudio </option>
    <option value=1234>Nobel, Felix de (assistent-dirigent)</option>
</select>
<select id="solistcode" name="solistcode" class="select2 s2long">
    <option class="placeholder" value="0" selected="selected">&lt;geen selectie&gt;</option>
    <option value=154>Aalst, Andr&eacute; van (viool)</option>
    <option value=704>Aarden, Mimi  (mezzosopraan)</option>
</select>
<select id="componistcode" name="componistcode" class="select2 s2default">
    <option class="placeholder" value="0" selected="selected">&lt;geen selectie&gt;</option>
    <option value=990>Abert, Johann Joseph (1832 - 1915)</option>
    <option value=1427>Aa, Michel van der (1970)</option>
    <option value=1508>Abad, Omar</option>
    <option value=42>Bonnisseau, Fr&eacute;d&eacute;ric ( ? - 1882)</option>
    <option value=99>Diversen (o.a. G. Fr&ouml;st)</option>
</select>
</form>
"""


def records(select_id: str, profession: str) -> list[RecordView]:
    parsed = [_record(select_id, profession, value, label) for value, label in _options(PAGE, select_id)]
    return [RecordView(doc) for doc in parsed if doc is not None]


def test_composer_with_life_years() -> None:
    record = records("componistcode", "composer")[0]
    assert record.external_id == "componistcode:990"
    assert record.name == "Abert, Johann Joseph"
    assert record.url is None
    assert record.raw == {
        "select": "componistcode",
        "value": "990",
        "label": "Abert, Johann Joseph (1832 - 1915)",
    }
    assert record.claims == (
        SourceClaim("has_profession", "profession", "composer"),
        SourceClaim("born_on", value="1832"),
        SourceClaim("died_on", value="1915"),
    )


def test_composer_with_birth_year_only() -> None:
    record = records("componistcode", "composer")[1]
    assert record.name == "Aa, Michel van der"
    assert record.claims == (
        SourceClaim("has_profession", "profession", "composer"),
        SourceClaim("born_on", value="1970"),
    )


def test_composer_without_years() -> None:
    record = records("componistcode", "composer")[2]
    assert record.name == "Abad, Omar"
    assert record.claims == (SourceClaim("has_profession", "profession", "composer"),)


def test_composer_with_unknown_birth_year() -> None:
    record = records("componistcode", "composer")[3]
    assert record.name == "Bonnisseau, Frédéric"  # entity unescaped
    assert record.claims == (
        SourceClaim("has_profession", "profession", "composer"),
        SourceClaim("died_on", value="1882"),
    )


def test_non_year_parenthetical_stays_in_the_name() -> None:
    record = records("componistcode", "composer")[4]
    assert record.name == "Diversen (o.a. G. Fröst)"
    assert record.claims == (SourceClaim("has_profession", "profession", "composer"),)


def test_soloist_discipline_becomes_a_claim() -> None:
    first, second = records("solistcode", "soloist")
    assert first.external_id == "solistcode:154"
    assert first.name == "Aalst, André van"
    assert first.claims == (
        SourceClaim("has_profession", "profession", "soloist"),
        SourceClaim("performs_as", value="viool"),
    )
    assert second.name == "Aarden, Mimi"  # double space before parenthetical
    assert SourceClaim("performs_as", value="mezzosopraan") in second.claims


def test_conductor_labels_are_kept_verbatim() -> None:
    first, second = records("dirigentcode", "conductor")
    assert first.external_id == "dirigentcode:2182"
    assert first.name == "Abbado, Claudio"  # trailing space stripped
    assert first.claims == (SourceClaim("has_profession", "profession", "conductor"),)
    # "(assistent-dirigent)" is not a year range: stays in the name
    assert second.name == "Nobel, Felix de (assistent-dirigent)"


def test_placeholder_option_is_skipped() -> None:
    values = [value for value, _ in _options(PAGE, "componistcode")]
    assert "0" not in values


# Trimmed copy of the List-view result table: a concert with three works (the
# first row opens it with a DATE and idx link; the next two inherit its date and
# city), then a second concert whose first work has two soloists (the second
# soloist is its own row with only the SOLOIST cell filled) and a final work
# with no composer.
LIST_PAGE = """\
<table id="zoekresultaat">
<tr>
    <th>DATE</th><th>CITY</th><th>COMPOSER</th><th>TITLE</th>
    <th>CONDUCTOR</th><th>SOLOIST</th><th></th>
</tr>
<tr>
    <td>30-06-1929</td><td>Amsterdam</td><td>Haydn, Joseph</td>
    <td>Symfonie nr. 092 in G gr.t., Hob. I:92</td><td>Beinum, Eduard van</td><td></td>
    <td><a href="/en/archive/search/?idx=0&sub=0">1</a></td>
</tr>
<tr>
    <td></td><td></td><td>Rimski-Korsakov, Nikolaj</td>
    <td>Pianoconcert in cis kl.t., op. 30</td><td>Beinum, Eduard van</td><td>Hagedorn, Meta</td><td></td>
</tr>
<tr>
    <td></td><td></td><td>Saint-Sa&euml;ns, Camille</td>
    <td>Symfonie nr. 3 in c kl.t., op. 78</td><td>Beinum, Eduard van</td><td></td><td></td>
</tr>
<tr>
    <td>05-10-1939</td><td>Amsterdam</td><td>Mahler, Gustav</td>
    <td>Das Lied von der Erde</td><td>Schuricht, Carl</td><td>Oehman, Martin</td>
    <td><a href="/en/archive/search/?idx=3&sub=3">1</a></td>
</tr>
<tr>
    <td></td><td></td><td></td><td></td><td></td><td>Thorborg, Kerstin</td><td></td>
</tr>
<tr>
    <td></td><td></td><td></td><td>Magna res est amor</td><td>Schuricht, Carl</td><td></td><td></td>
</tr>
</table>
"""


def performances() -> list[MentionView]:
    return [MentionView(doc) for doc in _performances(LIST_PAGE)]


def test_concert_start_row_becomes_a_work_mention() -> None:
    mention = performances()[0]
    assert mention.external_id == "perf:0"
    assert mention.title == "Symfonie nr. 092 in G gr.t., Hob. I:92"
    assert mention.composer == "Haydn, Joseph"
    assert mention.raw["idx"] == 0
    assert mention.raw["date"] == "30-06-1929"
    assert mention.raw["city"] == "Amsterdam"
    assert mention.raw["conductor"] == "Beinum, Eduard van"
    assert mention.raw["soloists"] == []  # no soloist on this work


def test_continuation_work_inherits_concert_date_and_city() -> None:
    mention = performances()[1]
    assert mention.external_id == "perf:1"
    assert mention.title == "Pianoconcert in cis kl.t., op. 30"
    assert mention.composer == "Rimski-Korsakov, Nikolaj"
    assert mention.raw["soloists"] == [{"name": "Hagedorn, Meta", "discipline": None}]
    # date/city carried over from the concert-start row
    assert mention.raw["date"] == "30-06-1929"
    assert mention.raw["city"] == "Amsterdam"


def test_composer_name_is_html_unescaped_and_in_dropdown_format() -> None:
    mention = performances()[2]
    # "Last, First" like the dropdown, so dedup_key unifies the two entities
    assert mention.composer == "Saint-Saëns, Camille"


def test_multi_soloist_work_folds_extra_rows_into_one_mention() -> None:
    mention = performances()[3]
    assert mention.title == "Das Lied von der Erde"
    assert mention.composer == "Mahler, Gustav"
    assert mention.raw["conductor"] == "Schuricht, Carl"
    assert [s["name"] for s in mention.raw["soloists"]] == ["Oehman, Martin", "Thorborg, Kerstin"]


def test_work_without_composer_has_none_composer() -> None:
    mention = performances()[4]
    assert mention.external_id == "perf:4"
    assert mention.title == "Magna res est amor"
    assert mention.composer is None
    # still inherits the second concert's date/city
    assert mention.raw["date"] == "05-10-1939"


def test_only_work_rows_advance_the_index() -> None:
    mentions = performances()
    # five works total; the extra-soloist row did not create a sixth
    assert [m.external_id for m in mentions] == ["perf:0", "perf:1", "perf:2", "perf:3", "perf:4"]


# A small page with soloists that carry voice/instrument types in parentheses.
LIST_PAGE_WITH_VOICE = """\
<table id="zoekresultaat">
<tr>
    <th>DATE</th><th>CITY</th><th>COMPOSER</th><th>TITLE</th>
    <th>CONDUCTOR</th><th>SOLOIST</th><th></th>
</tr>
<tr>
    <td>05-10-1939</td><td>Amsterdam</td><td>Mahler, Gustav</td>
    <td>Das Lied von der Erde</td><td>Schuricht, Carl</td><td>Oehman, Martin (tenor)</td>
    <td></td>
</tr>
<tr>
    <td></td><td></td><td></td><td></td><td></td><td>Thorborg, Kerstin (alt)</td><td></td>
</tr>
</table>
"""


def test_soloist_voice_type_is_split_into_name_and_discipline() -> None:
    mention = MentionView(list(_performances(LIST_PAGE_WITH_VOICE))[0])
    # the parenthetical is stripped from the name and kept as the discipline
    assert mention.raw["soloists"] == [
        {"name": "Oehman, Martin", "discipline": "tenor"},
        {"name": "Thorborg, Kerstin", "discipline": "alt"},
    ]


def test_soloist_without_voice_type_has_no_discipline() -> None:
    # The original LIST_PAGE has soloists with no parentheticals.
    mention = MentionView(list(_performances(LIST_PAGE))[1])
    assert mention.raw["soloists"] == [{"name": "Hagedorn, Meta", "discipline": None}]
