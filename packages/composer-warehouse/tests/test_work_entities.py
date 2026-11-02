"""Linking each source's ``work`` entity to the canonical work it describes."""

from datetime import UTC, datetime
from typing import Any

from composer_models import Entity, RawWorkMention, Source, Work, WorkEntity
from composer_schema import EntityDocument, SourceClaim
from composer_warehouse.derived import refresh_derived
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention
from composer_warehouse.works.entities import link_work_entities
from composer_warehouse.works.merge import merge_works
from sqlalchemy import select, update
from sqlalchemy.orm import Session

BRAHMS = "Brahms, Johannes"
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def work_entity(external_id: str, label: str, source: str = "publisher") -> EntityDocument:
    return EntityDocument(
        id=external_id,
        url=f"https://{source}.example/{external_id}",
        source_name=source,
        ingested_at=NOW,
        name=label,
        kind="work",
        claims=(SourceClaim("includes_instrument", "instrumentation", "horn"),),
    )


def ingest(session: Session, source: str, *records: Any) -> None:
    ingest_source(session, FakeSource(records=records, name=source, base_url=f"https://{source}.example"))


def links(session: Session) -> set[tuple[str, str, str]]:
    """(work title, entity label, source name) for every link."""
    return {
        (title, label, source)
        for title, label, source in session.execute(
            select(Work.canonical_title, Entity.label, Source.name)
            .select_from(WorkEntity)
            .join(Work, Work.id == WorkEntity.work_id)
            .join(Entity, Entity.id == WorkEntity.entity_id)
            .join(Source, Source.id == WorkEntity.source_id)
        )
    }


def test_an_entity_links_to_the_work_its_mention_resolved_to(session: Session) -> None:
    ingest(
        session,
        "publisher",
        mention("Symphony No. 2 in D major, Op. 73", BRAHMS, "BA1"),
        work_entity("BA1", "Symphony No. 2 (Brahms, Johannes)"),
    )
    stats = link_work_entities(session)

    assert links(session) == {
        ("Symphony No. 2 in D major, Op. 73", "Symphony No. 2 (Brahms, Johannes)", "publisher")
    }
    assert (stats.links, stats.unlinked) == (1, 0)


def test_a_wikidata_mention_per_composer_links_to_the_items_entity(session: Session) -> None:
    item = {"item": "http://www.wikidata.org/entity/Q1"}
    ingest(
        session,
        "wikidata_instrumentation",
        perf_mention("Q1:Q7294", "F-A-E Sonata", BRAHMS, item),
        perf_mention("Q1:Q7351", "F-A-E Sonata", "Schumann, Robert", item),
        work_entity("Q1", "F-A-E Sonata (Johannes Brahms / Robert Schumann)", "wikidata_instrumentation"),
    )
    link_work_entities(session)

    linked = links(session)
    assert {label for _, label, _ in linked} == {"F-A-E Sonata (Johannes Brahms / Robert Schumann)"}
    # the entity links to every work a composer's mention resolved to
    linked_works = set(session.scalars(select(WorkEntity.work_id)))
    assert linked_works == set(session.scalars(select(RawWorkMention.work_id)))


def test_an_anthology_item_and_another_sources_id_link_nothing(session: Session) -> None:
    ingest(
        session,
        "publisher",
        mention("Intermezzo", BRAHMS, "BA9#0"),
        work_entity("BA9", "Klavierstücke (Brahms, Johannes)"),
    )
    ingest(session, "other", mention("Symphony No. 2", BRAHMS, "BA1"))
    ingest(session, "publisher", work_entity("BA1", "Symphony No. 2 (Brahms, Johannes)"))

    stats = link_work_entities(session)
    assert links(session) == set()
    assert stats.unlinked == 2


def test_an_unmatched_mention_links_nothing(session: Session) -> None:
    ingest(session, "publisher", mention("Symphony No. 2", BRAHMS, "BA1"), work_entity("BA1", "Symphony 2"))
    session.execute(update(RawWorkMention).values(work_id=None, match_status="needs_review"))

    assert link_work_entities(session).links == 0


def test_the_link_follows_a_rematched_mention_and_is_rebuilt_not_kept(session: Session) -> None:
    ingest(
        session,
        "publisher",
        mention("Symphony No. 2", BRAHMS, "BA1"),
        work_entity("BA1", "Symphony No. 2 (Brahms, Johannes)"),
    )
    ingest(session, "other", mention("Violin Concerto in D major, Op. 77", BRAHMS, "o1"))
    link_work_entities(session)

    concerto = session.scalars(select(Work).where(Work.canonical_title.startswith("Violin"))).one()
    session.execute(
        update(RawWorkMention).where(RawWorkMention.external_id == "BA1").values(work_id=concerto.id)
    )
    link_work_entities(session)

    assert links(session) == {
        ("Violin Concerto in D major, Op. 77", "Symphony No. 2 (Brahms, Johannes)", "publisher")
    }
    assert link_work_entities(session).links == 1


def test_one_entity_two_sources_state_is_linked_once_per_source(session: Session) -> None:
    """Work entities are keyed on their label, so two catalogues printing the
    same composer-qualified title share one entity; each source's link stands."""
    label = "Symphony No. 2 (Brahms, Johannes)"
    ingest(session, "publisher", mention("Symphony No. 2, Op. 73", BRAHMS, "BA1"), work_entity("BA1", label))
    ingest(
        session, "henle", mention("Symphony No. 2, Op. 73", BRAHMS, "HN1"), work_entity("HN1", label, "henle")
    )
    link_work_entities(session)

    assert links(session) == {
        ("Symphony No. 2, Op. 73", label, "publisher"),
        ("Symphony No. 2, Op. 73", label, "henle"),
    }


def test_a_folded_works_links_go_with_it_and_return_on_the_merged_work(session: Session) -> None:
    """``merge_works`` deletes the works it folds, before this pass rebuilds the
    links; a link left pointing at a deleted work would break its foreign key
    (on Postgres, which checks it)."""
    mozart = "Mozart, Wolfgang Amadeus"
    ingest(session, "a", mention("Symphony no. 40 in G minor K. 550", mozart, "a1"))
    ingest(
        session,
        "b",
        mention("Symphony No. 40 (K.550)", "Mozart", "b1"),
        work_entity("b1", "Sinfonie 40", "b"),
    )
    link_work_entities(session)
    folded = session.scalars(select(WorkEntity.work_id)).one()

    entities = {e.label: e for e in session.scalars(select(Entity).where(Entity.kind == "person"))}
    entities["Mozart"].canonical_entity_id = entities[mozart].id
    session.commit()
    merge_works(session)

    # SQLite does not check the key, so look for the stale row itself
    assert session.get(Work, folded) is None
    assert session.scalars(select(WorkEntity).where(WorkEntity.work_id == folded)).all() == []
    stats = refresh_derived(session)
    assert stats.work_entities.links == 1
    assert links(session) == {("Symphony no. 40 in G minor K. 550", "Sinfonie 40", "b")}
