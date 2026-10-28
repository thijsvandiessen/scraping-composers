"""Genres and movements keyed on QIDs, legacy label linking, and the orphan report (#349)."""

from __future__ import annotations

import itertools

from composer_models import Claim, Entity
from composer_models.normalize import dedup_key, entity_uuid, qid_key
from composer_schema import EntityDocument, SourceClaim
from composer_warehouse.derived import refresh_derived
from composer_warehouse.genres import genre_report, link_label_genres
from composer_warehouse.testing import FakeSource, genre, ingest_source, person
from sqlalchemy import select
from sqlalchemy.orm import Session

WALTZ = ("Q215", "waltz")
DANCE = ("Q316930", "dance music")


def _tagged(name: str, *genres: tuple[str, str], legacy: tuple[str, ...] = ()) -> EntityDocument:
    """A person tagged with QID-pinned genres, and label-only ones as an old
    ``wikidata`` bronze run (or a non-Wikidata source) reports them."""
    claims = [SourceClaim("has_genre", "genre", label, object_wikidata_id=qid) for qid, label in genres]
    claims += [SourceClaim("has_genre", "genre", label) for label in legacy]
    return person(name, *claims)


def _genre_rows(session: Session) -> set[tuple[str, str, str | None]]:
    """(dedup key, label, canonical's dedup key) for every genre entity."""
    canonical = {e.id: e.dedup_key for e in session.scalars(select(Entity))}
    return {
        (e.dedup_key, e.label, canonical.get(e.canonical_entity_id) if e.canonical_entity_id else None)
        for e in session.scalars(select(Entity).where(Entity.kind.in_(("genre", "movement"))))
    }


def test_a_qid_keeps_one_entity_across_label_changes(session: Session) -> None:
    """A renamed Wikidata label used to split the genre in two."""
    ingest_source(session, FakeSource(records=(_tagged("A", ("Q215", "waltz")),), name="run1"))
    ingest_source(session, FakeSource(records=(_tagged("B", ("Q215", "Waltz (dance)")),), name="run2"))

    (entity,) = session.scalars(select(Entity).where(Entity.kind == "genre"))
    assert entity.dedup_key == qid_key("Q215")
    assert entity.id == entity_uuid("genre", "wikidata:Q215")
    assert entity.label == "waltz"  # display text: the first label seen


def test_the_record_and_the_claim_object_are_the_same_entity(session: Session) -> None:
    ingest_source(session, FakeSource(records=(_tagged("A", WALTZ),), name="wikidata"))
    ingest_source(session, FakeSource(records=(genre(*WALTZ, DANCE), genre(*DANCE)), name="wikidata_genres"))

    keys = {e.dedup_key for e in session.scalars(select(Entity).where(Entity.kind == "genre"))}
    assert keys == {"wikidata:Q215", "wikidata:Q316930"}
    edge = session.scalars(select(Claim).where(Claim.predicate == "subclass_of")).one()
    assert (edge.subject_id, edge.object_id) == (
        entity_uuid("genre", "wikidata:Q215"),
        entity_uuid("genre", "wikidata:Q316930"),
    )


def test_a_genre_without_a_qid_keeps_its_label_key(session: Session) -> None:
    ingest_source(session, FakeSource(records=(_tagged("A", legacy=("Symphonic Poem",)),)))
    (entity,) = session.scalars(select(Entity).where(Entity.kind == "genre"))
    assert entity.dedup_key == dedup_key("Symphonic Poem")


def test_label_only_genres_link_to_the_qid_entity_by_label_or_alias(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(records=(genre(*WALTZ, DANCE, aliases=("valse",)), genre(*DANCE)), name="wikidata_genres"),
    )
    ingest_source(session, FakeSource(records=(_tagged("A", legacy=("Waltz", "Valse", "polka")),)))

    stats = link_label_genres(session)

    assert (stats.linked, stats.unmatched, stats.ambiguous) == (2, 1, 0)
    by_key = {e.dedup_key: e for e in session.scalars(select(Entity).where(Entity.kind == "genre"))}
    waltz_id = entity_uuid("genre", "wikidata:Q215")
    assert by_key["waltz"].canonical_entity_id == waltz_id
    assert by_key["valse"].canonical_entity_id == waltz_id
    assert by_key["polka"].canonical_entity_id is None


def test_a_label_two_qids_share_links_nothing(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(records=(genre("Q1", "romance"), genre("Q2", "Romance")), name="wikidata_genres"),
    )
    ingest_source(session, FakeSource(records=(_tagged("A", legacy=("romance",)),)))

    stats = link_label_genres(session)

    assert stats.ambiguous == 1
    assert (
        session.scalars(select(Entity).where(Entity.dedup_key == "romance")).one().canonical_entity_id is None
    )


def test_linking_starts_from_scratch(session: Session) -> None:
    """A link whose target has gone (its label changed) does not linger."""
    ingest_source(session, FakeSource(records=(genre(*WALTZ),), name="wikidata_genres"))
    ingest_source(session, FakeSource(records=(_tagged("A", legacy=("waltz",)),)))
    link_label_genres(session)
    target = session.get(Entity, entity_uuid("genre", "wikidata:Q215"))
    assert target is not None
    target.label = "valse"
    session.commit()

    assert link_label_genres(session).linked == 0
    assert (
        session.scalars(select(Entity).where(Entity.dedup_key == "waltz")).one().canonical_entity_id is None
    )


def test_ingest_order_does_not_change_the_genre_set() -> None:
    """Old label-only runs and new QID runs, replayed in any order, end in the
    same entities and links — what a rebuild from bronze relies on."""
    from composer_models.db import get_engine, init_db

    sources = [
        FakeSource(records=(_tagged("A", legacy=("waltz", "opera")),), name="old_wikidata"),
        FakeSource(records=(_tagged("A", WALTZ, ("Q1344", "opera")),), name="wikidata"),
        FakeSource(records=(genre(*WALTZ, DANCE), genre(*DANCE)), name="wikidata_genres"),
    ]
    outcomes = []
    for order in itertools.permutations(sources):
        with init_db(get_engine("sqlite://"))() as session:
            for source in order:
                ingest_source(session, source)
            refresh_derived(session)
            outcomes.append(_genre_rows(session))
    assert all(outcome == outcomes[0] for outcome in outcomes)
    assert ("waltz", "waltz", "wikidata:Q215") in outcomes[0]


def test_report_counts_orphans_and_ranks_them_by_persons(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(
            records=(
                genre("Q2", "Viennese waltz", WALTZ),
                genre(*WALTZ, DANCE),
                genre(*DANCE),
                genre("Q50", "sardana"),
                genre("Q51", "tonadilla"),
            ),
            name="wikidata_genres",
        ),
    )
    ingest_source(
        session,
        FakeSource(
            records=(
                _tagged("A", ("Q2", "Viennese waltz"), ("Q50", "sardana")),
                _tagged("B", ("Q50", "sardana"), legacy=("sardana", "lost genre")),
                _tagged("C", ("Q51", "tonadilla")),
            )
        ),
    )
    link_label_genres(session)

    report = genre_report(session, "genre", {"Q316930": "dance music", "Q9730": "classical music"})

    assert report.total == 7
    assert (report.qid_keyed, report.label_only, report.legacy_linked) == (5, 2, 1)
    assert report.with_parent == 2
    # sardana, tonadilla and the unlinkable "lost genre"; the linked label-only
    # "sardana" shares its canonical's place
    assert report.orphans == 3
    assert report.missing_top_level == ["Q9730"]
    assert [(o.label, o.wikidata_id, o.persons) for o in report.top_orphans] == [
        ("sardana", "Q50", 2),  # A and B, counted once although B names it twice
        ("lost genre", None, 1),
        ("tonadilla", "Q51", 1),
    ]
    # five distinct tags, of which only A's "Viennese waltz" reaches dance music
    assert (report.tags, report.tags_reached) == (5, 1)
    assert report.reach == 0.2


def test_report_counts_musical_forms_apart_from_orphans(session: Session) -> None:
    """A form ("symphony") has no path to a top-level genre either, but it is
    a term of another axis, not a gap in the genre tree (#364)."""
    ingest_source(
        session,
        FakeSource(
            records=(
                genre("Q9734", "symphony", form=True),
                genre("Q8701407", "waltz", DANCE, form=True),
                genre(*DANCE),
                genre("Q50", "sardana"),
            ),
            name="wikidata_genres",
        ),
    )
    ingest_source(
        session,
        FakeSource(
            records=(
                _tagged("A", ("Q9734", "symphony"), ("Q8701407", "waltz")),
                _tagged("B", ("Q9734", "symphony"), ("Q50", "sardana")),
            )
        ),
    )

    report = genre_report(session, "genre", {"Q316930": "dance music"})

    assert (report.orphans, report.forms) == (1, 1)  # a form that reaches the top is neither
    assert [(o.label, o.persons) for o in report.top_orphans] == [("sardana", 1)]
    assert [(o.label, o.wikidata_id, o.persons) for o in report.top_forms] == [("symphony", "Q9734", 2)]
    assert (report.tags, report.tags_reached) == (4, 1)


def test_an_empty_report_has_no_reach(session: Session) -> None:
    assert genre_report(session, "movement", {"Q9730": "classical music"}).reach == 0.0
