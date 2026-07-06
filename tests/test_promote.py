"""Tests for the bronze → gold promote pipeline."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from composer_ingest.etl.db import init_db
from composer_ingest.etl.gold import promote, read_gold_manifest
from composer_ingest.etl.models import (
    Claim,
    Concert,
    ConcertParticipant,
    ConcertWork,
    Entity,
    RawWorkMention,
    Work,
)
from composer_ingest.scraper.sources import SourceClaim, WorkMentionDocument
from conftest import ingest_source
from test_ingest import FakeSource, person
from test_ingest_mentions import mention


def perf_mention(external_id: str, title: str, composer: str, raw: dict[str, Any]) -> WorkMentionDocument:
    """A work mention with a realistic performance-context payload."""
    return WorkMentionDocument(
        id=external_id,
        url=None,
        source_name="fake",
        ingested_at=datetime(2024, 1, 1, tzinfo=UTC),
        title=title,
        composer=composer,
        raw=raw,
    )


def _seed_bronze(session: Session) -> None:
    """Bronze with every promote-relevant case:

    - Beethoven: mentioned as a composer of two work mentions (kept, rule 1a)
    - Mahler, Gustav: reported by the same performance archive (kept, rule 1b)
      with claims referencing Vienna (place kept via rule 3)
    - Nobody, Obscure: from a non-performance source only (dropped, rule 1),
      referencing Atlantis (place pruned, rule 3)
    - "Beethoven" duplicate linked to "Beethoven, Ludwig van" (collapsed, rule 2)
    """
    archive = FakeSource(
        records=(
            mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),
            mention("Sinfonie Nr. 5, op. 67", "Beethoven, Ludwig van", "m2"),
            person("Mahler, Gustav", SourceClaim("born_in", "place", "Vienna"), external_id="a:mahler"),
        ),
        name="archive",
        base_url="https://archive.example",
    )
    encyclopedia = FakeSource(
        records=(
            person("Nobody, Obscure", SourceClaim("born_in", "place", "Atlantis"), external_id="e:nobody"),
            person("Beethoven", SourceClaim("has_profession", "profession", "composer"), external_id="e:b"),
        ),
        name="encyclopedia",
        base_url="https://encyclopedia.example",
    )
    ingest_source(session, archive)
    ingest_source(session, encyclopedia)

    # link the short-name duplicate to the canonical entity (what dedupe-persons does)
    canonical = session.scalars(select(Entity).where(Entity.label == "Beethoven, Ludwig van")).one()
    duplicate = session.scalars(select(Entity).where(Entity.label == "Beethoven")).one()
    duplicate.canonical_entity_id = canonical.id
    session.commit()


def _gold_session(gold_path: Path) -> Session:
    return init_db(create_engine(f"sqlite:///{gold_path}"))()


def test_promote_applies_all_three_rules(session: Session, tmp_path: Path) -> None:
    _seed_bronze(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        # rule 1: kept via mention (Beethoven) and via archive record (Mahler)
        assert "Beethoven, Ludwig van" in labels
        assert "Mahler, Gustav" in labels
        assert "Nobody, Obscure" not in labels  # encyclopedia-only person dropped
        # rule 2: the duplicate row is gone, nothing carries a canonical link
        assert "Beethoven" not in labels
        assert gold.scalar(select(Entity.id).where(Entity.canonical_entity_id.is_not(None))) is None
        # rule 3: place referenced by a kept person survives, the orphan doesn't
        assert "Vienna" in labels
        assert "Atlantis" not in labels
        # the dropped person's profession claim object is pruned unless referenced
        # by a kept claim — the duplicate's claim was re-pointed, so it survives
        assert "composer" in labels

    assert stats.persons_kept == 2
    assert stats.duplicates_collapsed == 1
    assert stats.persons_dropped == 1  # Nobody, Obscure
    assert stats.entities_pruned >= 1  # Atlantis


def test_promote_repoints_claims_and_mentions_to_canonical(session: Session, tmp_path: Path) -> None:
    _seed_bronze(session)
    gold_path = tmp_path / "gold.db"
    promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        beethoven = gold.scalars(select(Entity).where(Entity.label == "Beethoven, Ludwig van")).one()
        # the duplicate's has_profession claim now hangs off the canonical entity
        profession = gold.scalars(
            select(Claim).where(Claim.subject_id == beethoven.id, Claim.predicate == "has_profession")
        ).one()
        assert profession.object is not None and profession.object.label == "composer"
        # works and mentions still resolve to the canonical composer
        work = gold.scalars(select(Work)).one()
        assert work.composer_entity_id == beethoven.id
        assert set(gold.scalars(select(RawWorkMention.composer_entity_id))) == {beethoven.id}
        assert gold.scalar(select(RawWorkMention.id).where(RawWorkMention.id == 1)) is not None


def _seed_concert_bronze(session: Session) -> None:
    """Bronze with performance payloads in each source's real shape."""
    concertgebouw = FakeSource(
        records=(
            perf_mention(
                "perf:0",
                "Symfonie nr. 5",
                "Beethoven, Ludwig van",
                {"date": "30-06-1929", "city": "Amsterdam", "conductor": "Beinum, Eduard van"},
            ),
            perf_mention(
                "perf:1",
                "Egmont Ouverture",
                "Beethoven, Ludwig van",
                {"date": "30-06-1929", "city": "Amsterdam", "conductor": "Beinum, Eduard van"},
            ),
            perf_mention(
                "perf:2",
                "Vioolconcert",
                "Beethoven, Ludwig van",
                {"date": "01-07-1929", "city": "Amsterdam", "conductor": "Mengelberg, Willem"},
            ),
            person("Beinum, Eduard van", external_id="cg:beinum"),
            person("Mengelberg, Willem", external_id="cg:mengelberg"),
        ),
        name="concertgebouw_archive",
        base_url="https://cg.example",
    )
    berlinphil = FakeSource(
        records=(
            perf_mention(
                "perf:10-1",
                "Paradise and the Peri",
                "Robert Schumann",
                {
                    "concert_id": "10",
                    "date": "2009-02-08",
                    "season": "2008/09",
                    "url": "https://www.digitalconcerthall.com/en/concert/10",
                    "conductors": ["Sir Simon Rattle"],  # natural order; entity is surname-first
                    "soloists": [{"name": "Sally Matthews", "discipline": "soprano"}],
                },
            ),
            person("Rattle, Sir Simon", external_id="bp:rattle"),
            person("Matthews, Sally", external_id="bp:matthews"),
        ),
        name="berlinphil",
        base_url="https://bp.example",
    )
    nyphil = FakeSource(
        records=(
            perf_mention(
                "perf:100:0:0",
                "Luisa Miller",
                "Verdi, Giuseppe",
                {
                    "programID": "100",
                    "date": "1993-06-26",
                    "season": "1992-93",
                    "eventType": "Parks",
                    "venue": "Great Lawn",
                    "location": "Manhattan, NY",
                    "conductors": ["Ghost, Unresolvable"],  # no matching person entity
                },
            ),
        ),
        name="nyphil",
        base_url="https://nyp.example",
    )
    for source in (concertgebouw, berlinphil, nyphil):
        ingest_source(session, source)


def test_promote_derives_concerts_from_mentions(session: Session, tmp_path: Path) -> None:
    _seed_concert_bronze(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        concerts = gold.scalars(select(Concert).order_by(Concert.date)).all()
        # cg: two dates -> two concerts; bp: one; nyphil: one
        assert len(concerts) == 4
        first = concerts[0]
        assert first.date == "1929-06-30"  # DD-MM-YYYY normalized to ISO
        assert first.venue == "Amsterdam"
        assert len(first.works) == 2  # both mentions of that evening grouped
        assert {p.name for p in first.participants} == {"Beinum, Eduard van"}

        beinum = gold.scalars(select(Entity).where(Entity.label == "Beinum, Eduard van")).one()
        assert first.participants[0].entity_id == beinum.id

        # natural-order conductor name resolves to the surname-first entity
        rattle_concert = next(c for c in concerts if c.url is not None)
        rattle = gold.scalars(select(Entity).where(Entity.label == "Rattle, Sir Simon")).one()
        by_role = {p.role: p for p in rattle_concert.participants}
        assert by_role["conductor"].entity_id == rattle.id
        assert rattle_concert.season == "2008/09"

        # soloists are participants too, with their discipline and entity link
        matthews = gold.scalars(select(Entity).where(Entity.label == "Matthews, Sally")).one()
        assert by_role["soloist"].name == "Sally Matthews"
        assert by_role["soloist"].discipline == "soprano"
        assert by_role["soloist"].entity_id == matthews.id

        # unresolved conductor keeps the row, without an entity link
        nyp = next(c for c in concerts if c.venue == "Great Lawn, Manhattan, NY")
        assert nyp.participants[0].name == "Ghost, Unresolvable"
        assert nyp.participants[0].entity_id is None
        assert (nyp.season, nyp.event_type) == ("1992-93", "Parks")

    assert stats.concerts == 4
    assert stats.concert_participant_links == 4  # Beinum, Mengelberg, Rattle, Matthews
    assert stats.unresolved_participant_names == 1


def test_concert_tables_stay_empty_in_bronze(session: Session, tmp_path: Path) -> None:
    _seed_concert_bronze(session)
    promote(session, tmp_path / "gold.db")
    assert session.scalar(select(Concert.id)) is None
    assert session.scalar(select(ConcertParticipant.id)) is None
    assert session.scalar(select(ConcertWork.id)) is None


def test_promote_writes_manifest_and_is_rerunnable(session: Session, tmp_path: Path) -> None:
    _seed_bronze(session)
    gold_path = tmp_path / "gold.db"
    first = promote(session, gold_path)
    second = promote(session, gold_path)  # full rebuild: same result, no leftovers

    assert first == second
    manifest = read_gold_manifest(gold_path)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.stats["persons_kept"] == 2
    assert not gold_path.with_suffix(".db.tmp").exists()
