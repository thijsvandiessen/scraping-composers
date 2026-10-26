"""Works matched across a composer's duplicate entities (#362): candidates
grouped by canonical composer at ingest, and the post-dedupe pass folding the
works a replay split apart."""

from typing import Any

from composer_models import Entity, RawWorkMention, Work, WorkIdentifier, WorkLink, WorkTitle
from composer_warehouse.derived import refresh_derived
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention
from composer_warehouse.works import load_candidates
from composer_warehouse.works.links import link_works
from composer_warehouse.works.merge import _apply, merge_works  # pyright: ignore[reportPrivateUsage]
from sqlalchemy import func, select
from sqlalchemy.orm import Session

WOLFGANG = "Mozart, Wolfgang Amadeus"


def ingest(session: Session, source: str, *mentions: Any) -> None:
    ingest_source(session, FakeSource(records=mentions, name=source, base_url=f"https://{source}.example"))


def link_person(session: Session, duplicate: str, canonical: str) -> None:
    """What ``dedupe_persons`` leaves behind: the duplicate pointing at its canonical."""
    entities = {e.label: e for e in session.scalars(select(Entity).where(Entity.kind == "person"))}
    entities[duplicate].canonical_entity_id = entities[canonical].id
    session.commit()


def works(session: Session) -> int:
    return session.scalar(select(func.count(Work.id))) or 0


def work_of(session: Session, title: str) -> Work:
    return session.scalars(
        select(Work)
        .join(RawWorkMention, RawWorkMention.work_id == Work.id)
        .where(RawWorkMention.raw_title == title)
    ).one()


def test_kv_550_by_two_entities_of_mozart_becomes_one_work(session: Session) -> None:
    ingest(session, "a", mention("Symphony no. 40 in G minor K. 550", WOLFGANG, "a1"))
    ingest(session, "b", mention("Symphony No. 40 (K.550)", "Mozart", "b1"))
    ingest(session, "c", mention("Sinfonie in g KV 550 (1. Fassung)", "Mozart", "c1"))
    assert works(session) == 3  # ingested before the persons were linked

    link_person(session, "Mozart", WOLFGANG)
    stats = merge_works(session)

    assert (stats.within_composer, stats.composerless) == (1, 0)
    assert works(session) == 2
    symphony = work_of(session, "Symphony no. 40 in G minor K. 550")
    assert work_of(session, "Symphony No. 40 (K.550)").id == symphony.id
    assert symphony.composer is not None and symphony.composer.label == WOLFGANG  # the canonical one
    titles = set(session.scalars(select(WorkTitle.title).where(WorkTitle.work_id == symphony.id)))
    assert titles == {"Symphony no. 40 in G minor K. 550", "Symphony No. 40 (K.550)"}
    # the version stays its own work, and links to the one symphony
    link_works(session)
    link = session.scalars(select(WorkLink)).one()
    assert (link.relation, link.target_work_id) == ("version_of", symphony.id)


def test_a_composerless_work_folds_into_the_owner_of_its_catalogue(session: Session) -> None:
    ingest(
        session,
        "a",
        mention("Symphony no. 40 in G minor K. 550", WOLFGANG, "a1"),
        mention("Symphony no. 41 in C major K. 551", WOLFGANG, "a2"),
        # an arranger holding one Köchel number does not own the catalogue
        mention("Symphony no. 41, KV 551 (arr. for piano)", "Hummel, Johann Nepomuk", "a3"),
    )
    ingest(
        session,
        "c",
        mention("Symfonie nr. 40 in g kl.t., KV 550", None, "c1"),
        mention("Symfonie nr. 41, KV 551", None, "c2"),
        # nobody holds KV 999: nothing to fold into
        mention("Sinfonie KV 999", None, "c3"),
    )

    stats = merge_works(session)

    assert (stats.within_composer, stats.composerless) == (0, 2)
    assert (
        work_of(session, "Symfonie nr. 40 in g kl.t., KV 550").id
        == work_of(session, "Symphony no. 40 in G minor K. 550").id
    )
    assert (
        work_of(session, "Symfonie nr. 41, KV 551").id
        == work_of(session, "Symphony no. 41 in C major K. 551").id
    )
    assert work_of(session, "Sinfonie KV 999").composer_entity_id is None


def test_a_scheme_numbered_per_composer_has_no_owner(session: Session) -> None:
    ingest(session, "a", mention("Variations WoO 80", "Beethoven, Ludwig van", "a1"))
    ingest(session, "c", mention("Variationen WoO 80", None, "c1"))

    assert merge_works(session).merged == 0
    assert works(session) == 2


def test_two_composers_sharing_a_number_are_not_merged_without_a_person_link(session: Session) -> None:
    ingest(session, "a", mention("Symphony no. 40 in G minor K. 550", WOLFGANG, "a1"))
    ingest(session, "b", mention("Symphony No. 40 (K.550)", "Mozart", "b1"))

    assert merge_works(session).merged == 0
    assert works(session) == 2


def test_folding_moves_identifiers_mentions_and_the_earlier_premiere(session: Session) -> None:
    ingest(
        session,
        "a",
        perf_mention("a1", "Requiem in D minor, K. 626", WOLFGANG, {"premiere": {"date": "1793-12-14"}}),
    )
    ingest(
        session,
        "b",
        perf_mention("b1", "Requiem, KV 626", "Mozart", {"premiere": {"date": "1793-01-02"}}),
        perf_mention("b2", "Requiem K626", "Mozart", {}),
    )
    link_person(session, "Mozart", WOLFGANG)
    merge_works(session)

    requiem = session.scalars(select(Work)).one()
    assert requiem.premiere_date == "1793-01-02"
    assert set(session.scalars(select(RawWorkMention.work_id))) == {requiem.id}
    identifiers = session.execute(select(WorkIdentifier.work_id, WorkIdentifier.scheme, WorkIdentifier.value))
    assert {tuple(row) for row in identifiers} == {(requiem.id, "KV", "626")}
    assert session.scalar(select(func.count(WorkIdentifier.id))) == 2  # one row per stating source
    assert merge_works(session).merged == 0  # idempotent


def test_ingest_matches_a_duplicates_mention_against_its_canonical(session: Session) -> None:
    ingest(session, "a", mention("Symphony no. 40 in G minor K. 550", WOLFGANG, "a1"))
    ingest(session, "b", mention("Eine kleine Nachtmusik K. 525", "Mozart", "b1"))
    link_person(session, "Mozart", WOLFGANG)

    candidates = load_candidates(session)
    wolfgang = session.scalars(select(Entity.id).where(Entity.label == WOLFGANG)).one()
    assert len(candidates.of(wolfgang)) == 2
    ingest(session, "c", mention("Symphony No. 40 (K.550)", "Mozart", "c1"))

    assert works(session) == 2
    assert (
        work_of(session, "Symphony No. 40 (K.550)").id
        == work_of(session, "Symphony no. 40 in G minor K. 550").id
    )


def test_refresh_derived_folds_before_it_links(session: Session) -> None:
    ingest(session, "a", mention("Symphony no. 40 in G minor K. 550", WOLFGANG, "a1"))
    ingest(session, "b", mention("Symphony No. 40 (K.550)", "Mozart", "b1"))
    ingest(session, "b", mention("Andante (from Symphony No. 40, K. 550)", "Mozart", "b2"))
    link_person(session, "Mozart", WOLFGANG)

    derived = refresh_derived(session)

    assert derived.merged.merged == 1
    assert derived.works.by_catalogue == 1
    link = session.scalars(select(WorkLink)).one()
    assert link.target_work_id == work_of(session, "Symphony no. 40 in G minor K. 550").id


def test_a_target_folded_in_a_later_round_takes_its_folded_works_along(session: Session) -> None:
    ingest(
        session,
        "a",
        mention("Requiem K. 626", WOLFGANG, "a1"),
        mention("Mass in C minor K. 427", WOLFGANG, "a2"),
        mention("Ave verum corpus K. 618", WOLFGANG, "a3"),
    )
    titles = ("Requiem K. 626", "Mass in C minor K. 427", "Ave verum corpus K. 618")
    a, b, c = (work_of(session, t).id for t in titles)
    _apply(session, {c: b, b: a})  # b absorbed c, then b itself folded into a
    session.commit()

    assert works(session) == 1
    assert set(session.scalars(select(RawWorkMention.work_id))) == {a}
