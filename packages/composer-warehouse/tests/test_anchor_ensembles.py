"""Tests for the ensemble anchoring pass (#350): name-keyed ensembles folded
onto the Wikidata ensembles they name, and nothing else."""

import pytest
from composer_models import Entity
from composer_models.normalize import qid_key
from composer_schema import EntityDocument, SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.ensembles import (
    AnchorStats,
    alias_index,
    anchor_ensembles,
    ensemble_match_key,
    members_of,
)
from composer_warehouse.testing import FakeSource, ensemble, ingest_source, perf_mention, person
from sqlalchemy import select
from sqlalchemy.orm import Session

VPO = "Q900001"


def _wikidata(qid: str, label: str, *claims: SourceClaim) -> EntityDocument:
    return ensemble(label, *claims, external_id=qid, url=f"https://www.wikidata.org/wiki/{qid}")


def _alias(name: str) -> SourceClaim:
    return SourceClaim("also_known_as", value=name)


def _city(name: str) -> SourceClaim:
    return SourceClaim("based_in", "place", name)


VIENNA_PHILHARMONIC = _wikidata(
    VPO,
    "Vienna Philharmonic",
    SourceClaim("ensemble_type", value="orchestra"),
    _alias("Wiener Philharmoniker"),
    _alias("Vienna Philharmonic Orchestra"),
    _city("Vienna"),
)

SPELLINGS = (
    "Vienna Philharmonic",
    "The Vienna Philharmonic",
    "Vienna Philharmonic Orchestra",
    "Wiener Philharmoniker",
)
OTHER_GROUPS = (
    "Wiener Philharmoniker Streichquartett",
    "Junge Philharmonie Wien",
    "Vienna Chamber Philharmonic Orchestra",
)
MEMBERS = "Members Of The Wiener Philharmoniker"


def _ingest(session: Session, wikidata: tuple[EntityDocument, ...], credited: tuple[str, ...]) -> None:
    ingest_source(session, FakeSource(records=wikidata, name="wikidata_ensembles"))
    ingest_source(
        session,
        FakeSource(
            records=tuple(ensemble(name, external_id=f"rec:{name}") for name in credited),
            name="imslp_recordings",
        ),
    )


def _canonical(session: Session, label: str) -> Entity | None:
    entity = session.scalars(
        select(Entity).where(Entity.label == label, Entity.dedup_key != qid_key(VPO))
    ).one()
    return session.get(Entity, entity.canonical_entity_id) if entity.canonical_entity_id else None


@pytest.fixture
def vienna(session: Session) -> Session:
    _ingest(session, (VIENNA_PHILHARMONIC,), (*SPELLINGS, *OTHER_GROUPS, MEMBERS))
    return session


def test_the_wikidata_ensemble_is_keyed_on_its_qid(vienna: Session) -> None:
    anchor = vienna.scalars(select(Entity).where(Entity.dedup_key == qid_key(VPO))).one()
    assert (anchor.kind, anchor.label) == ("ensemble", "Vienna Philharmonic")


def test_the_four_spellings_resolve_to_the_anchor(vienna: Session) -> None:
    stats = anchor_ensembles(vienna)

    anchor = vienna.scalars(select(Entity).where(Entity.dedup_key == qid_key(VPO))).one()
    for spelling in SPELLINGS:
        assert _canonical(vienna, spelling) == anchor, spelling
    assert stats.anchors == 1
    assert stats.anchored == 4


def test_groups_whose_names_overlap_stay_apart(vienna: Session) -> None:
    stats = anchor_ensembles(vienna)

    for other in (*OTHER_GROUPS, MEMBERS):
        assert _canonical(vienna, other) is None, other
    assert stats.unanchored == len(OTHER_GROUPS)
    assert stats.members_of_kept_separate == 1


def test_a_rerun_is_idempotent(vienna: Session) -> None:
    first = anchor_ensembles(vienna)
    links = set(vienna.execute(select(Entity.id, Entity.canonical_entity_id)).tuples())
    assert anchor_ensembles(vienna) == first
    assert set(vienna.execute(select(Entity.id, Entity.canonical_entity_id)).tuples()) == links


def test_a_link_goes_when_its_evidence_does(session: Session) -> None:
    _ingest(session, (), ("Vienna Philharmonic", "Berliner Philharmoniker"))
    entity = session.scalars(select(Entity).where(Entity.label == "Vienna Philharmonic")).one()
    other = session.scalars(select(Entity).where(Entity.label == "Berliner Philharmoniker")).one()
    entity.canonical_entity_id = other.id  # a stale link from an earlier pass
    session.commit()

    anchor_ensembles(session)
    assert _canonical(session, "Vienna Philharmonic") is None


def test_a_shared_name_is_split_by_the_home_city_it_spells(session: Session) -> None:
    wiener = _wikidata("Q900002", "Wiener Symphoniker", _alias("Vienna Symphony"), _city("Vienna"))
    virginia = _wikidata(
        "Q900003", "Vienna Symphony Orchestra (Virginia)", _alias("Vienna Symphony"), _city("Fairfax")
    )
    _ingest(session, (wiener, virginia), ("Vienna Symphony",))

    stats = anchor_ensembles(session)
    target = _canonical(session, "Vienna Symphony")
    assert target is not None and target.label == "Wiener Symphoniker"
    assert stats.ambiguous == 0


def test_a_shared_name_no_city_settles_is_left_alone(session: Session) -> None:
    berlin = _wikidata("Q900004", "Rundfunkchor Berlin", _alias("Radio Choir"), _city("Berlin"))
    leipzig = _wikidata("Q900005", "MDR Rundfunkchor", _alias("Radio Choir"), _city("Leipzig"))
    _ingest(session, (berlin, leipzig), ("Radio Choir",))

    stats = anchor_ensembles(session)
    assert _canonical(session, "Radio Choir") is None
    assert stats.ambiguous == 1
    assert "radio choir" not in alias_index(session)  # two anchors carry it


def test_the_boundary_is_reported_not_moved(session: Session) -> None:
    arts = _wikidata("Q900006", "Les Arts Florissants", SourceClaim("ensemble_type", value="choir"))
    _ingest(session, (arts,), ("Johann Strauss",))
    ingest_source(
        session,
        FakeSource(
            records=(
                person("Les Arts Florissants", external_id="p:arts"),
                person("Johann Strauss", url="https://www.wikidata.org/wiki/Q900007"),
            ),
            name="decca",
        ),
    )

    stats = anchor_ensembles(session)
    assert stats.persons_named_like_anchors == 1
    assert stats.ensembles_named_like_persons == 1
    kinds = session.scalars(select(Entity.kind).where(Entity.label == "Les Arts Florissants")).all()
    assert sorted(kinds) == ["ensemble", "person"]


def test_unanchored_ensembles_on_a_programme_are_counted(session: Session) -> None:
    _ingest(session, (VIENNA_PHILHARMONIC,), ("Junge Philharmonie Wien",))
    ingest_source(
        session,
        FakeSource(
            records=(
                perf_mention(
                    "perf:1",
                    "Symphony No. 9",
                    "Beethoven, Ludwig van",
                    {"concert_id": "1", "date": "2020-01-01", "ensembles": ["Junge Philharmonie Wien"]},
                ),
            ),
            name="berlinphil",
        ),
    )
    derive_concerts(session)

    stats = anchor_ensembles(session)
    assert (stats.unanchored, stats.unanchored_with_appearances) == (1, 1)


def test_no_anchors_means_nothing_is_linked(session: Session) -> None:
    _ingest(session, (), SPELLINGS)
    assert anchor_ensembles(session) == AnchorStats(unanchored=len(SPELLINGS))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("The Vienna Philharmonic", "Vienna Philharmonic"),
        ("Vienna Philharmonic Orchestra", "Vienna Philharmonic"),
        ("Wiener Philharmoniker", "Wiener Philharmonic Orchestra"),
        ("Gewandhausorchester", "Gewandhausorchester"),
        ("Orchestre de Paris", "Orchestra de Paris"),
    ],
)
def test_match_keys_meet(a: str, b: str) -> None:
    assert ensemble_match_key(a) == ensemble_match_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Vienna Chamber Philharmonic Orchestra", "Vienna Philharmonic"),
        ("Junge Philharmonie Wien", "Wiener Philharmoniker"),
        ("Wiener Philharmoniker Streichquartett", "Wiener Philharmoniker"),
    ],
)
def test_match_keys_keep_other_groups_apart(a: str, b: str) -> None:
    assert ensemble_match_key(a) != ensemble_match_key(b)


def test_members_of_reads_the_whole_group() -> None:
    assert members_of("Members Of The Wiener Philharmoniker") == "wiener philharmoniker"
    assert members_of("Mitglieder der Wiener Philharmoniker") == "wiener philharmoniker"
    assert members_of("Wiener Philharmoniker") is None


def test_the_home_city_is_the_pinned_place_composers_are_born_in(session: Session) -> None:
    vienna = SourceClaim("based_in", "place", "Vienna", object_wikidata_id="Q900020")
    _ingest(session, (_wikidata(VPO, "Vienna Philharmonic", vienna),), ())
    ingest_source(
        session,
        FakeSource(
            records=(
                person(
                    "Mahler, Gustav",
                    SourceClaim("died_in", "place", "Vienna", object_wikidata_id="Q900020"),
                    url="https://www.wikidata.org/wiki/Q900021",
                ),
            ),
            name="wikidata",
        ),
    )
    places = session.scalars(select(Entity).where(Entity.kind == "place")).all()
    assert [p.dedup_key for p in places] == ["vienna|Q900020"]
