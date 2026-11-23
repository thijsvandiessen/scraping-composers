"""Anchoring person identity on Wikidata before pairwise matching (#205)."""

import uuid

from composer_models import Entity, PersonMatch
from composer_schema import EntityDocument, SourceClaim
from composer_warehouse.persons import AuthorityIndex, dedupe_persons, load_records, parse_name
from composer_warehouse.persons.authority import name_keys
from composer_warehouse.persons.corpus import PersonRecord
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy import select
from sqlalchemy.orm import Session


def _ingest(session: Session, *people: EntityDocument, name: str = "fake") -> None:
    ingest_source(session, FakeSource(name=name, records=people))


def _wikidata(label: str, qid: str, *claims: SourceClaim) -> EntityDocument:
    """A person record keyed by its QID, as ingest keys a wikidata page."""
    return person(label, *claims, external_id=qid, url=f"https://www.wikidata.org/wiki/{qid}")


def _dates(born: str, died: str | None = None) -> tuple[SourceClaim, ...]:
    claims = [SourceClaim("born_on", value=born)]
    if died:
        claims.append(SourceClaim("died_on", value=died))
    return tuple(claims)


def _alias(value: str) -> SourceClaim:
    return SourceClaim("also_known_as", value=value)


def _crowd(n: int = 40) -> list[EntityDocument]:
    """Filler sharing one common surname, so the corpus has term frequencies."""
    return [person(f"Smith, Filler{i}") for i in range(n)]


def _by_label(session: Session) -> dict[str, Entity]:
    return {e.label: e for e in session.scalars(select(Entity).where(Entity.kind == "person"))}


def _record(label: str, *aliases: str, qid: str | None = None) -> PersonRecord:
    return PersonRecord(
        entity_id=uuid.uuid4(),
        label=label,
        name=parse_name(label),
        aliases=tuple(parse_name(alias) for alias in aliases),
        wikidata_ids=frozenset({qid} if qid else ()),
    )


def test_name_keys_ignore_word_order_and_particles_but_not_initials() -> None:
    assert name_keys(_record("Beethoven, Ludwig van")) & name_keys(_record("Ludwig van Beethoven"))
    assert not name_keys(_record("Bach, J.S.")) & name_keys(_record("Bach, Johann Sebastian"))


def test_a_bare_surname_is_never_looked_up() -> None:
    """ "Mozart" names more than one Mozart; it cannot pick one (#202)."""
    assert name_keys(_record("Mozart")) == frozenset()
    index = AuthorityIndex.build([_record("Wolfgang Amadeus Mozart", "Mozart", qid="Q254")])
    assert index.lookup(_record("Mozart")) == frozenset()


def test_the_index_finds_an_item_by_alias_and_by_a_reversed_reading() -> None:
    index = AuthorityIndex.build(
        [
            _record("Pyotr Ilyich Tchaikovsky", "Peter Ilyich Tchaikovsky", qid="Q7315"),
            _record("Ye Xiaogang", qid="Q8053045"),
        ]
    )
    assert index.lookup(_record("Tchaikovsky, Peter Ilyich")) == {"Q7315"}
    assert index.lookup(_record("Xiaogang Ye")) == {"Q8053045"}
    assert index.lookup(_record("Peter Tchaikovsky")) == frozenset()


def test_a_name_held_by_several_items_finds_them_all() -> None:
    index = AuthorityIndex.build(
        [_record("John Williams", qid="Q131285"), _record("John Williams", qid="Q311987")]
    )
    assert index.lookup(_record("Williams, John")) == {"Q131285", "Q311987"}


def test_a_spelling_naming_one_item_is_anchored_to_it(session: Session) -> None:
    _ingest(
        session,
        _wikidata(
            "Pyotr Ilyich Tchaikovsky",
            "Q7315",
            _alias("Peter Ilyich Tchaikovsky"),
            *_dates("1840-05-07", "1893-11-06"),
        ),
        *_crowd(),
    )
    _ingest(session, person("Tchaikovsky, Peter Ilyich", *_dates("1840", "1893")), name="other")
    result = dedupe_persons(session)

    people = _by_label(session)
    anchor = people["Pyotr Ilyich Tchaikovsky"]
    assert people["Tchaikovsky, Peter Ilyich"].canonical_entity_id == anchor.id
    row = session.scalars(select(PersonMatch)).one()
    assert row.status == "auto_linked"
    assert row.method is not None and row.method.startswith("authority+given:exact")
    assert (result.anchor.linked, result.anchor.queued, result.anchor.ambiguous) == (1, 0, 0)
    assert result.anchor.resolution.authority == 1


def test_the_anchor_is_scored_not_trusted(session: Session) -> None:
    """A name lookup proposes the pair; the model still decides it. A record
    named like the item but born half a century later is someone else."""
    _ingest(
        session,
        _wikidata("Pyotr Ilyich Tchaikovsky", "Q7315", *_dates("1840", "1893")),
        *_crowd(),
    )
    _ingest(session, person("Tchaikovsky, Pyotr Ilyich", *_dates("1890", "1950")), name="other")
    result = dedupe_persons(session)

    assert result.anchor.linked == 0
    assert all(e.canonical_entity_id is None for e in _by_label(session).values())


def _williamses() -> list[EntityDocument]:
    return [
        _wikidata("John Williams", "Q131285", *_dates("1932-02-08")),
        _wikidata("John Williams", "Q311987", *_dates("1941-04-24")),
    ]


def test_a_name_several_items_hold_stays_unlinked(session: Session) -> None:
    """The "John Williams" case: the composer and the guitarist both carry the
    name, so a record carrying only it is a detectable "cannot resolve", not a
    coin flip. Its birth year fits only the composer, so the model links it to
    him and nothing else, and the namesake guard of #202, which needs links to
    two items, never fires: the hold is what keeps it unlinked."""
    _ingest(session, *_williamses(), *_crowd())
    _ingest(
        session,
        person("Williams, John", *_dates("1932-02-08")),
        name="other",
    )
    result = dedupe_persons(session)

    assert result.anchor.ambiguous == 1
    assert result.anchor.linked == 0
    assert all(e.canonical_entity_id is None for e in _by_label(session).values())
    held = _by_label(session)["Williams, John"].id
    touching = session.scalars(
        select(PersonMatch).where((PersonMatch.entity_id == held) | (PersonMatch.canonical_entity_id == held))
    ).all()
    assert touching, "the model proposed nothing, so the hold is untested"
    assert {m.status for m in touching} == {"needs_review"}
    assert result.anchor.resolution.authority == 0


def test_the_report_splits_authority_pairwise_and_unresolved(session: Session) -> None:
    _ingest(
        session,
        _wikidata("Pyotr Ilyich Tchaikovsky", "Q7315", *_dates("1840", "1893")),
        *_crowd(),
    )
    _ingest(
        session,
        person("Tchaikovsky, Pyotr Ilyich", *_dates("1840", "1893")),
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
        person("Sonnenfeld, Kurt"),
        name="other",
    )
    result = dedupe_persons(session)

    resolution = result.anchor.resolution
    # Tchaikovsky to his item; the Bachs to each other; Kurt and the 40 fillers to nobody
    assert (resolution.authority, resolution.pairwise, resolution.unresolved) == (1, 2, 41)
    # every record without a QID but Tchaikovsky: no item carries their names
    assert result.anchor.unmatched == len(load_records(session)) - 2
