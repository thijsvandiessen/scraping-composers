"""Tests for parsing the Concertgebouw archive search page dropdowns."""

from composer_ingest.sources import SourceClaim, SourceRecord
from composer_ingest.sources.concertgebouw import _options, _record

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


def records(select_id: str, profession: str) -> list[SourceRecord]:
    parsed = [_record(select_id, profession, value, label) for value, label in _options(PAGE, select_id)]
    return [record for record in parsed if record is not None]


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
        SourceClaim("performs_as", "discipline", "viool"),
    )
    assert second.name == "Aarden, Mimi"  # double space before parenthetical
    assert SourceClaim("performs_as", "discipline", "mezzosopraan") in second.claims


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
