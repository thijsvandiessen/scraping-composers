"""Works identified by catalogue number and external ids, end to end (#351):
ingest resolves on the strong keys and stores them, and the link pass points
parts, arrangements and versions at their parent."""

import uuid
from dataclasses import replace
from typing import Any

from composer_models import RawWorkMention, Source, Work, WorkIdentifier, WorkLink
from composer_warehouse.ingestion import new_work
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention
from composer_warehouse.works import Candidate, extract_features
from composer_warehouse.works.links import _range_links, link_works  # pyright: ignore[reportPrivateUsage]
from composer_warehouse.works.schemes import holds_sets
from sqlalchemy import select
from sqlalchemy.orm import Session

MOZART = "Mozart, Wolfgang Amadeus"
BACH = "Bach, Johann Sebastian"


def ingest(session: Session, source: str, *mentions: Any) -> None:
    ingest_source(session, FakeSource(records=mentions, name=source, base_url=f"https://{source}.example"))


def work_of(session: Session, title: str) -> Work:
    return session.scalars(
        select(Work)
        .join(RawWorkMention, RawWorkMention.work_id == Work.id)
        .where(RawWorkMention.raw_title == title)
    ).one()


def links(session: Session) -> set[tuple[str, str, str, str]]:
    titles = {work.id: work.canonical_title for work in session.scalars(select(Work))}
    return {
        (titles[link.work_id], link.relation, titles[link.target_work_id], link.method)
        for link in session.scalars(select(WorkLink))
    }


def wikidata(qid: str, **raw: Any) -> dict[str, Any]:
    return {"item": f"http://www.wikidata.org/entity/{qid}", **raw}


def test_the_four_mozart_40s_become_one_work_and_a_version_of_it(session: Session) -> None:
    ingest(session, "a", mention("Symphony no. 40 in G minor K. 550", MOZART, "a1"))
    ingest(session, "b", mention("Sinfonie in g KV 550 (1. Fassung)", MOZART, "b1"))
    ingest(session, "c", mention("Symfonie nr. 40 in g kl.t., KV 550", MOZART, "c1"))
    ingest(session, "d", mention("Symphony No. 40 (K.550)", MOZART, "d1"))

    assert len(session.scalars(select(Work)).all()) == 2
    symphony = work_of(session, "Symphony No. 40 (K.550)")
    assert work_of(session, "Symfonie nr. 40 in g kl.t., KV 550").id == symphony.id
    assert (symphony.catalogue_prefix, symphony.catalogue_number) == ("KV", "550")

    stats = link_works(session)
    assert stats.links == 1
    assert links(session) == {
        ("Sinfonie in g KV 550 (1. Fassung)", "version_of", "Symphony no. 40 in G minor K. 550", "catalogue")
    }


def test_a_movement_and_an_arrangement_link_to_bwv_1048_without_merging(session: Session) -> None:
    concerto = "Brandenburg Concerto No. 3 in G major, BWV 1048"
    movement = "Allegro (From Brandenburg Concerto No. 3, BWV 1048)"
    arrangement = "Brandenburg Concerto No. 3, BWV 1048 (WOODWIND QUINTET) (ARR. Ferrari)"
    ingest(
        session,
        "a",
        mention(concerto, BACH, "a1"),
        mention(movement, BACH, "a2"),
        mention(arrangement, BACH, "a3"),
    )

    assert len(session.scalars(select(Work)).all()) == 3
    link_works(session)
    assert links(session) == {
        (movement, "part_of", concerto, "catalogue"),
        (arrangement, "arrangement_of", concerto, "catalogue"),
    }


def test_a_derived_work_with_two_possible_parents_links_to_neither(session: Session) -> None:
    ingest(
        session,
        "a",
        mention("Partita No. 1, BWV 1002", BACH, "a1"),
        mention("Sarabande (from Partita No. 1, BWV 1002)", BACH, "a2"),
    )
    # A second work holding BWV 1002, as a reviewer's "create new" leaves one.
    partita = work_of(session, "Partita No. 1, BWV 1002")
    duplicate = new_work(
        partita.composer_entity_id, "Partita in B minor", extract_features("Partita in B minor")
    )
    session.add(duplicate)
    source_id = session.scalars(select(Source.id).where(Source.name == "a")).one()
    session.add(WorkIdentifier(work_id=duplicate.id, scheme="BWV", value="1002", source_id=source_id))
    session.flush()

    stats = link_works(session)
    assert (stats.links, stats.ambiguous) == (0, 1)


def test_imslps_structured_catalogue_field_is_a_strong_key(session: Session) -> None:
    raw = {"opus_catalogue_number": "K.550", "imslp_catalogue_number": "IMR 12"}
    ingest(session, "imslp_works", perf_mention("Symphony No.40 (Mozart)", "Symphony No.40", MOZART, raw))
    ingest(session, "roh", mention("Sinfonie g-moll, KV 550", MOZART, "r1"))

    work = session.scalars(select(Work)).one()
    roh = session.scalars(select(RawWorkMention).where(RawWorkMention.external_id == "r1")).one()
    assert (roh.work_id, roh.match_method) == (work.id, "catalogue")
    imslp = session.scalars(select(Source.id).where(Source.name == "imslp_works")).one()
    stored = session.execute(
        select(WorkIdentifier.scheme, WorkIdentifier.value, WorkIdentifier.source_id)
    ).all()
    assert ("KV", "550", imslp) in {tuple(row) for row in stored}


def test_identifiers_survive_into_the_next_ingest_run(session: Session) -> None:
    """The title alone says nothing about KV 550; the stored identifier does,
    so a later run (a fresh ``load_state``) still matches on it."""
    raw = {"opus_catalogue_number": "K.550"}
    ingest(session, "imslp_works", perf_mention("w1", "Symphony in G minor", MOZART, raw))
    ingest(session, "other", mention("Grosse g-Moll-Sinfonie KV 550", MOZART, "o1"))

    assert len(session.scalars(select(Work)).all()) == 1
    other = session.scalars(select(RawWorkMention).where(RawWorkMention.external_id == "o1")).one()
    assert other.match_method == "catalogue"


def test_a_shared_wikidata_item_matches_titles_that_share_nothing_else(session: Session) -> None:
    ingest(
        session, "wikidata_works", perf_mention("Q1:Q254", "Eine kleine Nachtmusik", MOZART, wikidata("Q1"))
    )
    ingest(
        session,
        "wikidata_catalogue",
        perf_mention("Q1:Q254", "Serenade for strings in G", MOZART, wikidata("Q1")),
    )

    work = session.scalars(select(Work)).one()
    assert work.canonical_title == "Eine kleine Nachtmusik"
    methods = set(session.scalars(select(RawWorkMention.match_method)))
    assert methods == {"new", "wikidata"}


def test_wikidata_catalogue_ids_are_stored_and_normalised(session: Session) -> None:
    raw = wikidata(
        "Q2",
        catalogue_codes=[{"code": "550", "catalogue": "Köchel catalogue", "catalogue_short": "KV"}],
        musicbrainz_work=["0C5B4F7E-1D4E-3C0B-9C2B-2D1F2E5A6B7C"],
        iswc=["T-034.524.680-1"],
    )
    ingest(session, "wikidata_catalogue", perf_mention("Q2:Q254", "Symphony No. 40", MOZART, raw))

    stored = set(session.execute(select(WorkIdentifier.scheme, WorkIdentifier.value)).tuples())
    assert stored == {
        ("wikidata", "Q2"),
        ("KV", "550"),
        ("musicbrainz", "0c5b4f7e-1d4e-3c0b-9c2b-2d1f2e5a6b7c"),
        ("iswc", "T0345246801"),
    }


def test_wikidata_codes_are_read_by_their_catalogue_item(session: Session) -> None:
    """Terry 238/3 has no short name to read it by, and a Rinaldi catalogue
    code keeps the catalogue item as its scheme (#361)."""
    jc_bach = "Johann Christian Bach"
    codes = [
        {
            "catalogue_item": "Q139766427",
            "code": "238/3",
            "catalogue": "Terry-Verzeichnis",
            "catalogue_short": None,
        },
        {
            "catalogue_item": "Q112328324",
            "code": "R. op. 243",
            "catalogue": "Rinaldi",
            "catalogue_short": None,
        },
    ]
    ingest(
        session,
        "wikidata_catalogue",
        perf_mention("Q3:Q1", "Quintet", jc_bach, wikidata("Q3", catalogue_codes=codes)),
    )
    ingest(
        session,
        "imslp_works",
        perf_mention("w1", "Quintet in D", jc_bach, {"opus_catalogue_number": "Ty 238/3"}),
    )

    stored = set(session.execute(select(WorkIdentifier.scheme, WorkIdentifier.value)).tuples())
    assert {("Ty", "238/3"), ("wd:Q112328324", "r. op. 243")} <= stored
    assert len(session.scalars(select(Work)).all()) == 1


def test_two_wikidata_items_meet_on_a_code_in_an_unmapped_catalogue(session: Session) -> None:
    code = [{"catalogue_item": "Q16749680", "code": "C 48", "catalogue": "Chomiński catalog"}]
    chopin = "Chopin, Frédéric"
    ingest(
        session,
        "wikidata_catalogue",
        perf_mention("Q4:Q1268", "Piano Concerto No. 2", chopin, wikidata("Q4", catalogue_codes=code)),
        perf_mention("Q5:Q1268", "Klavierkonzert f-Moll", chopin, wikidata("Q5", catalogue_codes=code)),
    )
    assert len(session.scalars(select(Work)).all()) == 1


def test_wikidata_part_of_links_a_movement_to_its_work(session: Session) -> None:
    ingest(
        session,
        "wikidata_catalogue",
        perf_mention("Q10:Q1339", "Brandenburg Concerto No. 3", BACH, wikidata("Q10")),
        perf_mention(
            "Q11:Q1339",
            "Brandenburg Concerto No. 3, first movement",
            BACH,
            wikidata("Q11", part_of=["http://www.wikidata.org/entity/Q10"]),
        ),
    )
    stats = link_works(session)
    assert stats.by_wikidata == 1
    assert links(session) == {
        ("Brandenburg Concerto No. 3, first movement", "part_of", "Brandenburg Concerto No. 3", "wikidata")
    }


def test_link_works_rebuilds_rather_than_accumulates(session: Session) -> None:
    ingest(
        session,
        "a",
        mention("Brandenburg Concerto No. 3, BWV 1048", BACH, "a1"),
        mention("Allegro (from Brandenburg Concerto No. 3, BWV 1048)", BACH, "a2"),
    )
    link_works(session)
    link_works(session)
    assert len(session.scalars(select(WorkLink)).all()) == 1


def test_pieces_link_to_their_range_instead_of_merging_into_it(session: Session) -> None:
    ingest(
        session,
        "a",
        mention("15 Inventions, BWV 772-786", BACH, "a1"),
        mention("Invention No. 1 in C major, BWV 772", BACH, "a2"),
        mention("Invention No. 15 in B minor, BWV 786", BACH, "a3"),
        mention("Sinfonia No. 1 in C major, BWV 787", BACH, "a4"),
    )
    assert len(session.scalars(select(Work)).all()) == 4

    stats = link_works(session)
    assert stats.by_range == 2
    assert links(session) == {
        ("Invention No. 1 in C major, BWV 772", "part_of", "15 Inventions, BWV 772-786", "catalogue_range"),
        ("Invention No. 15 in B minor, BWV 786", "part_of", "15 Inventions, BWV 772-786", "catalogue_range"),
    }


def test_imslps_structured_range_keeps_the_set_apart(session: Session) -> None:
    handel = "Handel, George Frideric"
    raw = {"opus_catalogue_number": "Op.6 ; HWV 319-330"}
    ingest(session, "imslp_works", perf_mention("w1", "12 Concerti Grossi, Op.6", handel, raw))
    ingest(session, "roh", mention("Concerto grosso in G major HWV 319", handel, "r1"))

    assert len(session.scalars(select(Work)).all()) == 2
    link_works(session)
    assert links(session) == {
        ("Concerto grosso in G major HWV 319", "part_of", "12 Concerti Grossi, Op.6", "catalogue_range")
    }


def test_a_number_inside_nested_sets_links_to_the_smallest(session: Session) -> None:
    """*15 Inventions* sits inside *Inventions and Sinfonias*: the invention is
    part of the smaller set, and a sinfonia (only in the bigger one) of that."""
    ingest(
        session,
        "a",
        mention("Inventions and Sinfonias BWV 772\u2013801", BACH, "a1"),
        mention("15 Inventions, BWV 772-786", BACH, "a2"),
        mention("Invention No. 1 in C major, BWV 772", BACH, "a3"),
        mention("Sinfonia No. 1 in C major, BWV 787", BACH, "a4"),
    )
    stats = link_works(session)
    assert (stats.by_range, stats.ambiguous) == (2, 0)
    assert links(session) == {
        ("Invention No. 1 in C major, BWV 772", "part_of", "15 Inventions, BWV 772-786", "catalogue_range"),
        (
            "Sinfonia No. 1 in C major, BWV 787",
            "part_of",
            "Inventions and Sinfonias BWV 772\u2013801",
            "catalogue_range",
        ),
    }


def test_a_number_inside_overlapping_sets_links_to_neither(session: Session) -> None:
    ingest(
        session,
        "a",
        mention("Inventions BWV 772-786", BACH, "a1"),
        mention("Keyboard pieces BWV 780-801", BACH, "a2"),
        mention("Invention No. 9 in F minor, BWV 780", BACH, "a3"),
        mention("Invention No. 1 in C major, BWV 772", BACH, "a4"),
    )
    stats = link_works(session)
    assert (stats.by_range, stats.ambiguous) == (1, 1)
    assert links(session) == {
        ("Invention No. 1 in C major, BWV 772", "part_of", "Inventions BWV 772-786", "catalogue_range")
    }


def test_an_external_id_with_hyphens_is_no_range() -> None:
    """A MusicBrainz id reads like ``...-799131026480``; taken as a range it
    would enumerate billions of members (seen on the 2026-10-25 silver, #378)."""
    assert holds_sets("BWV") and holds_sets("KV6") and holds_sets("KV6 Anh") and holds_sets("WoO")
    assert not (holds_sets("musicbrainz") or holds_sets("wd:Q1") or holds_sets("op") or holds_sets("other"))
    mbid = ("musicbrainz", "4dad1f72-5f8d-3a63-ae31-799131026480")
    oversized = ("BWV", "1-10000")
    holder = Candidate(
        uuid.uuid4(), replace(extract_features("Symphony"), identifiers=frozenset({mbid, oversized}))
    )
    single = frozenset({("musicbrainz", "799131026470"), ("BWV", "500")})
    piece = Candidate(uuid.uuid4(), replace(extract_features("Piece"), identifiers=single))
    found: dict[tuple[uuid.UUID, str, uuid.UUID], str] = {}
    assert _range_links([holder, piece], found) == 0
    assert found == {}
