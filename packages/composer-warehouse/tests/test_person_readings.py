"""Tests for alternate readings of a name (#201).

A plain label has more than one defensible parse: "Ye Xiaogang" may be
surname-first, "Adrian Le Roy" may be "Leroy". The matcher used to see only
the last-token parse, so such pairs were never blocked together and the scorer
gated them out before they were scored at all.
"""

import uuid
from dataclasses import replace

from composer_schema import SourceClaim
from composer_warehouse.persons.corpus import PersonRecord, build_corpus, candidate_pairs, surname_blocks
from composer_warehouse.persons.dedupe import dedupe_persons
from composer_warehouse.persons.extract import COLLAPSED, PRIMARY, REORDERED, comparable, parse_name
from composer_warehouse.persons.match import (
    AUTO_THRESHOLD,
    PersonProfile,
    PersonScorer,
    classify,
    deciding_names,
    default_model,
    pairings,
)
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy.orm import Session


def _record(label: str, born: int | None = None, died: int | None = None) -> PersonRecord:
    return PersonRecord(
        entity_id=uuid.uuid4(), label=label, name=parse_name(label), birth_year=born, death_year=died
    )


def _without_readings(record: PersonRecord) -> PersonRecord:
    """The record as the matcher saw it before #201: primary parses only."""
    return replace(
        record,
        name=replace(record.name, alternates=()),
        aliases=tuple(replace(alias, alternates=()) for alias in record.aliases),
    )


def _labels(pairs: object) -> set[frozenset[str]]:
    return {frozenset((a.label, b.label)) for a, b in pairs}  # type: ignore[attr-defined]


def _scorer(records: list[PersonRecord]) -> PersonScorer:
    return PersonScorer(default_model(), build_corpus(records))


# ---------------------------------------------------------------------------
# parse_name: the readings themselves
# ---------------------------------------------------------------------------


def test_a_plain_two_word_name_can_also_be_read_surname_first() -> None:
    name = parse_name("Ye Xiaogang")
    assert (name.reading, name.surname, name.given) == (PRIMARY, "xiaogang", ("ye",))
    assert [(r.reading, r.surname, r.given) for r in name.alternates] == [(REORDERED, "ye", ("xiaogang",))]


def test_a_comma_names_its_surname_so_it_is_never_reordered() -> None:
    assert all(r.reading != REORDERED for r in parse_name("Ye, Xiaogang").readings())
    assert parse_name("Asafev B. V.").alternates == ()  # "Surname I. I." is already surname-first


def test_a_surname_written_in_parts_can_also_be_read_as_one_word() -> None:
    le_roy = {r.surname: r for r in parse_name("Adrian Le Roy").alternates}
    assert le_roy["leroy"].reading == COLLAPSED
    assert le_roy["leroy"].given == ("adrian",)

    double = {r.surname: r for r in parse_name("Agathe Backer Grondahl").alternates}
    assert double["backergrondahl"].given == ("agathe",)

    comma = {r.surname for r in parse_name("Backer-Grondahl, Agathe").alternates}
    assert comma == {"backergrondahl"}


def test_readings_take_no_part_in_name_equality() -> None:
    # Same parts, different alternates: still the same name.
    name = parse_name("Ye Xiaogang")
    assert replace(name, alternates=()) == name


def test_a_generational_suffix_is_never_read_as_a_name() -> None:
    """Suffixes are what tell a namesake family apart, so no alternate reading
    may turn one into a surname or a given name, or drop it."""
    for label in ("Johann Strauss II", "Johann Strauss I", "Strauss, Johann II", "Johann Strauss Jr"):
        assert parse_name(label).alternates == (), label

    junior = parse_name("Jr Johann Strauss")
    assert junior.surname == "strauss"
    assert junior.alternates == ()


def test_two_surname_first_readings_are_not_comparable() -> None:
    # Both would put "john" in the surname slot: a shared first name.
    (a,) = (r for r in parse_name("John Smith").alternates if r.reading == REORDERED)
    (b,) = (r for r in parse_name("John Williams").alternates if r.reading == REORDERED)
    assert a.surname == b.surname == "john"
    assert not comparable(a, b)
    # Against a reading with "john" where it was written, it is comparable.
    assert comparable(a, parse_name("John, Smith"))


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------


def test_a_reversed_pair_is_now_blocked_together() -> None:
    records = [_record("Ye Xiaogang"), _record("Xiaogang Ye")]
    assert _labels(candidate_pairs([_without_readings(r) for r in records])) == set()
    assert _labels(candidate_pairs(records)) == {frozenset({"Ye Xiaogang", "Xiaogang Ye"})}


def test_a_run_together_pair_is_now_blocked_together() -> None:
    records = [_record("Adrian Le Roy"), _record("Adrian Leroy")]
    assert _labels(candidate_pairs([_without_readings(r) for r in records])) == set()
    assert _labels(candidate_pairs(records)) == {frozenset({"Adrian Le Roy", "Adrian Leroy"})}


def test_records_meeting_only_under_a_shared_first_name_are_not_paired() -> None:
    records = [_record("John Smith"), _record("John Williams"), _record("John Smith")]
    blocks = surname_blocks(records)
    assert "john" not in blocks  # nobody wrote it as a surname
    assert len(blocks["john|smith"].reordered) == 2
    assert blocks["john|smith"].anchored == []
    # Only the two John Smiths are paired, and under "smith", as before.
    assert _labels(candidate_pairs(records)) == {frozenset({"John Smith"})}


def test_a_surname_first_reading_only_meets_its_own_given_name() -> None:
    """ "Thomas Mann" read surname-first could be "Mann Thomas", never "Dylan
    Thomas" or "M. Thomas": crossing every such reading with every Thomas
    doubled the pass, and an initial on the other side let two different men
    auto-link on a birth year."""
    records = [_record("Thomas Mann"), _record("Dylan Thomas"), _record("Mann Thomas"), _record("M. Thomas")]
    pairs = _labels(candidate_pairs(records))
    assert frozenset({"Thomas Mann", "Mann Thomas"}) in pairs
    assert frozenset({"Thomas Mann", "Dylan Thomas"}) not in pairs
    assert frozenset({"Thomas Mann", "M. Thomas"}) not in pairs

    (reordered,) = parse_name("Thomas Mann").alternates
    assert comparable(reordered, parse_name("Mann Thomas"))
    assert not comparable(reordered, parse_name("M. Thomas"))


def test_only_a_two_word_name_is_read_surname_first() -> None:
    """Read surname-first, "Michael George Giacchino" would have the given
    names "george giacchino", which "George Michael" is a prefix of."""
    assert all(r.reading != REORDERED for r in parse_name("Michael George Giacchino").alternates)
    assert all(r.reading != REORDERED for r in parse_name("Le Roy").alternates)


def test_an_alias_is_not_read_surname_first() -> None:
    """Alias lists run to dozens of scripts and nicknames; reading each one
    backwards too let unrelated people meet through them."""
    alias = PersonProfile(parse_name("Erick Morillo"), aliases=(parse_name("Smooth Touch"),))
    other = PersonProfile(parse_name("Toni Cottura"), aliases=(parse_name("Smooth T"),))
    assert list(pairings(alias, other)) == []
    record = PersonRecord(
        entity_id=uuid.uuid4(),
        label="Erick Morillo",
        name=parse_name("Erick Morillo"),
        aliases=(parse_name("Smooth Touch"),),
    )
    assert "smooth|touch" not in record.block_keys()
    assert record.block_keys()["morillo|erick"] is True


def test_every_pair_blocked_before_is_still_blocked() -> None:
    records = [
        _record(label)
        for label in (
            "Bach, Johann Sebastian",
            "Johann Sebastian Bach",
            "Bach J. S.",
            "Ludwig van Beethoven",
            "Beethoven",
            "Ye Xiaogang",
            "Xiaogang Ye",
        )
    ]
    before = _labels(candidate_pairs([_without_readings(r) for r in records]))
    assert before <= _labels(candidate_pairs(records))


# ---------------------------------------------------------------------------
# Scoring: the surname gate across readings
# ---------------------------------------------------------------------------


def test_a_reversed_pair_is_scored_rather_than_gated_out() -> None:
    records = [_record("Ye Xiaogang", 1955), _record("Xiaogang Ye", 1955), _record("Ye Ming")]
    a, b = records[0].profile(), records[1].profile()
    value, method = _scorer(records).score(a, b)
    assert method.startswith("given:exact")
    assert value > 0.0

    old_a, old_b = _without_readings(records[0]).profile(), _without_readings(records[1]).profile()
    assert _scorer(records).score(old_a, old_b) == (0.0, "surname_gate")


def test_a_run_together_pair_is_scored_rather_than_gated_out() -> None:
    records = [_record("Adrian Le Roy", 1520, 1598), _record("Adrian Leroy", 1520, 1598)]
    value, method = _scorer(records).score(records[0].profile(), records[1].profile())
    assert method.startswith("given:exact+born:exact+died:exact")
    assert classify(value) != "distinct"


def test_pairings_put_the_primary_names_first() -> None:
    a = PersonProfile(parse_name("Johann Sebastian Bach"))
    b = PersonProfile(parse_name("Bach, Johann Sebastian"))
    first = next(pairings(a, b))
    assert (first[0].reading, first[1].reading) == (PRIMARY, PRIMARY)
    assert deciding_names(a, b) == (a.name, b.name)


def test_profiles_with_no_surname_in_common_fall_back_to_their_primaries() -> None:
    a, b = PersonProfile(parse_name("Bach, Johann")), PersonProfile(parse_name("Handel, Georg"))
    assert list(pairings(a, b)) == []
    assert deciding_names(a, b) == (a.name, b.name)


def test_the_strauss_family_gains_no_auto_link_from_readings() -> None:
    """Namesake families are where extra readings would do harm: the change
    must not make any two Johann Strausses easier to merge.

    The surname-first spellings are there to give the readings something to
    work with. "Strauss Johann" does gain a link, to the equally bare "Johann
    Strauss" (the same name in the other order, which is the point of #201),
    but nothing new joins two members of the family.
    """
    family = {
        "Johann Strauss I",
        "Johann Strauss II",
        "Johann Strauss III",
        "Johann Strauss",
        "Johann Strauss Jr",
    }
    records = [
        _record("Johann Strauss I", 1804, 1849),
        _record("Johann Strauss II", 1825, 1899),
        _record("Johann Strauss III", 1866, 1939),
        _record("Johann Strauss"),
        _record("Strauss, Johann"),
        _record("Strauss Johann"),
        _record("Johann Strauss Jr"),
        *(_record(f"Filler{i} Smith") for i in range(40)),
    ]
    scorer = _scorer(records)

    def auto_linked(recs: list[PersonRecord]) -> set[frozenset[str]]:
        return {
            frozenset((a.label, b.label))
            for a, b in candidate_pairs(recs)
            if scorer.score(a.profile(), b.profile())[0] >= AUTO_THRESHOLD
        }

    before = auto_linked([_without_readings(r) for r in records])
    gained = auto_linked(records) - before
    assert not any(pair <= family for pair in gained)
    assert all(pair - family <= {"Strauss Johann"} for pair in gained)


# ---------------------------------------------------------------------------
# The dedupe pass
# ---------------------------------------------------------------------------


def test_the_dedupe_pass_proposes_a_reversed_pair(session: Session) -> None:
    dates = (SourceClaim("born_on", value="1955"), SourceClaim("died_on", value="2020"))
    ingest_source(
        session,
        FakeSource(
            records=(
                person("Ye Xiaogang", *dates),
                person("Xiaogang Ye", *dates),
                *(person(f"Smith, Filler{i}") for i in range(40)),
            )
        ),
    )
    result = dedupe_persons(session)
    assert result.auto + result.review == 1
