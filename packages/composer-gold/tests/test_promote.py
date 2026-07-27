# pylint: disable=too-many-lines
"""Tests for the silver → gold promote pipeline."""

from pathlib import Path

from composer_gold import PromoteConfig, promote, read_gold_manifest
from composer_schema import SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.db import init_db
from composer_warehouse.models import (
    Claim,
    Concert,
    ConcertParticipant,
    Entity,
    RawWorkMention,
    Recording,
    Work,
)
from composer_warehouse.recordings import derive_recordings
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention, person
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def _seed_silver(session: Session) -> None:
    """Silver with every promote-relevant case:

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
    _seed_silver(session)
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
    _seed_silver(session)
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


def _seed_concert_silver(session: Session) -> None:
    """Silver with performance payloads in each source's real shape."""
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


def test_promote_copies_silver_derived_concerts(session: Session, tmp_path: Path) -> None:
    _seed_concert_silver(session)
    derive_concerts(session)
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


def _recording_raw() -> dict[str, object]:
    return {
        "_source": "llm",
        "_kind": "recording",
        "record_key": "https://dg.example/album#486 1234",
        "url": "https://dg.example/album",
        "title": "Beethoven: Symphony No. 9",
        "release_date": "2024-03-15",
        "label": "Deutsche Grammophon",
        "catalogue_number": "486 1234",
        "format": "CD",
        "artists": [
            {"name": "Simon Rattle", "role": "conductor", "discipline": None},
            {"name": "Janine Jansen", "role": "soloist", "discipline": "violin"},
        ],
    }


def _seed_recording_silver(session: Session) -> None:
    raw = _recording_raw()
    dg = FakeSource(
        records=(
            perf_mention(f"{raw['record_key']}#w0", "Symphony No. 9", "Beethoven", raw),
            perf_mention(f"{raw['record_key']}#w1", "Coriolan Overture", "Beethoven", raw),
            person("Simon Rattle", external_id="dg:rattle"),
            person("Janine Jansen", external_id="dg:jansen"),
        ),
        name="deutschegrammophon",
        base_url="https://dg.example",
    )
    ingest_source(session, dg)


def test_promote_copies_silver_derived_recordings(session: Session, tmp_path: Path) -> None:
    _seed_recording_silver(session)
    derive_recordings(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        recording = gold.scalars(select(Recording)).one()
        assert recording.title == "Beethoven: Symphony No. 9"
        assert recording.catalogue_number == "486 1234"
        assert recording.label == "Deutsche Grammophon"
        assert len(recording.works) == 2  # both mentions grouped by record_key

        rattle = gold.scalars(select(Entity).where(Entity.label == "Simon Rattle")).one()
        by_role = {p.role: p for p in recording.participants}
        assert by_role["conductor"].entity_id == rattle.id  # participant re-pointed to canonical
        assert by_role["soloist"].discipline == "violin"

    assert stats.recordings == 1
    assert stats.recording_participant_links == 2  # Rattle, Jansen
    assert stats.unresolved_recording_participant_names == 0


def test_promote_over_underived_silver_yields_no_recordings(session: Session, tmp_path: Path) -> None:
    _seed_recording_silver(session)  # deliberately no derive_recordings
    stats = promote(session, tmp_path / "gold.db")
    assert stats.recordings == 0


def test_promote_over_underived_silver_yields_no_concerts(session: Session, tmp_path: Path) -> None:
    _seed_concert_silver(session)  # deliberately no derive_concerts
    stats = promote(session, tmp_path / "gold.db")
    assert stats.concerts == 0


def test_promote_repoints_concert_participants(session: Session, tmp_path: Path) -> None:
    """A participant resolved to a duplicate collapses to its canonical root in
    gold; one resolved to a dropped person keeps the name but loses the link."""
    concertgebouw = FakeSource(
        records=(
            perf_mention(
                "perf:0",
                "Symfonie nr. 5",
                "Beethoven, Ludwig van",
                {"date": "30-06-1929", "city": "Amsterdam", "conductor": "Beinum, Eduard"},
            ),
            person("Beinum, Eduard", external_id="cg:beinum-short"),
            person("Beinum, Eduard van", external_id="cg:beinum"),
        ),
        name="concertgebouw_archive",
        base_url="https://cg.example",
    )
    nyphil = FakeSource(
        records=(
            perf_mention(
                "perf:100:0:0",
                "Luisa Miller",
                "Verdi, Giuseppe",
                {"programID": "100", "date": "1993-06-26", "conductors": ["Ghost, Dropped"]},
            ),
        ),
        name="nyphil",
        base_url="https://nyp.example",
    )
    # the ghost conductor exists only in a non-performance source, so rule 1 drops it
    encyclopedia = FakeSource(
        records=(person("Ghost, Dropped", external_id="e:ghost"),),
        name="encyclopedia",
        base_url="https://encyclopedia.example",
    )
    for source in (concertgebouw, nyphil, encyclopedia):
        ingest_source(session, source)

    # link the short-name duplicate to the fuller name (what dedupe-persons does)
    canonical = session.scalars(select(Entity).where(Entity.label == "Beinum, Eduard van")).one()
    duplicate = session.scalars(select(Entity).where(Entity.label == "Beinum, Eduard")).one()
    duplicate.canonical_entity_id = canonical.id
    session.commit()

    derive_concerts(session)
    # in silver the participant resolves to the duplicate spelling
    silver_conductor = session.scalars(
        select(ConcertParticipant).where(ConcertParticipant.name == "Beinum, Eduard")
    ).one()
    assert silver_conductor.entity_id == duplicate.id

    stats = promote(session, tmp_path / "gold.db")

    with _gold_session(tmp_path / "gold.db") as gold:
        conductor = gold.scalars(
            select(ConcertParticipant).where(ConcertParticipant.name == "Beinum, Eduard")
        ).one()
        assert conductor.entity_id == canonical.id  # re-pointed to the root
        ghost = gold.scalars(
            select(ConcertParticipant).where(ConcertParticipant.name == "Ghost, Dropped")
        ).one()
        assert ghost.entity_id is None  # dropped person: name kept, link nulled
    assert stats.unresolved_participant_names == 1


def _seed_sitelink_silver(session: Session) -> None:
    """Silver with a performed composer and an encyclopedia-only, but famous, one.

    - Beethoven: composer of a work mention (kept by rule 1, no sitelink needed)
    - Famous, Unperformed: only in the encyclopedia, no concerts/works, but with
      a Wikidata sitelink count of 200 (kept only when the threshold allows)
    """
    archive = FakeSource(
        records=(mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),),
        name="archive",
        base_url="https://archive.example",
    )
    encyclopedia = FakeSource(
        records=(
            person(
                "Famous, Unperformed",
                SourceClaim("sitelink_count", value="200"),
                external_id="e:famous",
            ),
        ),
        name="encyclopedia",
        base_url="https://encyclopedia.example",
    )
    ingest_source(session, archive)
    ingest_source(session, encyclopedia)


def test_sitelink_threshold_off_by_default(session: Session, tmp_path: Path) -> None:
    _seed_sitelink_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)  # no threshold: promotion unchanged

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Beethoven, Ludwig van" in labels  # kept by work evidence
        assert "Famous, Unperformed" not in labels  # no evidence, threshold off
    assert stats.persons_promoted_by_sitelinks == 0


def test_sitelink_threshold_promotes_significant_person(session: Session, tmp_path: Path) -> None:
    _seed_sitelink_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(min_sitelinks=100))  # 200 >= 100

    with _gold_session(gold_path) as gold:
        famous = gold.scalars(select(Entity).where(Entity.label == "Famous, Unperformed")).one()
        # the sitelink_count claim rides along with the promoted person
        sitelink = gold.scalars(
            select(Claim).where(Claim.subject_id == famous.id, Claim.predicate == "sitelink_count")
        ).one()
        assert sitelink.value == "200"
    assert stats.persons_promoted_by_sitelinks == 1
    assert stats.persons_kept == 2  # Beethoven (evidence) + Famous (sitelinks)


def test_sitelink_threshold_below_count_drops_person(session: Session, tmp_path: Path) -> None:
    _seed_sitelink_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(min_sitelinks=300))  # 200 < 300

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Famous, Unperformed" not in labels  # numeric compare, not mere presence
        assert "Beethoven, Ludwig van" in labels  # evidence still wins independently
    assert stats.persons_promoted_by_sitelinks == 0


def test_rule1_off_keeps_unevidenced_persons(session: Session, tmp_path: Path) -> None:
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(drop_unevidenced_persons=False))

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Nobody, Obscure" in labels  # no evidence required anymore
        # rule 3 now keeps Atlantis: its only referrer is kept
        assert "Atlantis" in labels
        # the other rules still apply
        assert "Beethoven" not in labels  # duplicate still collapsed
    assert stats.persons_dropped == 0
    assert stats.persons_kept == 3  # Beethoven root, Mahler, Nobody
    assert stats.duplicates_collapsed == 1
    assert stats.persons_promoted_by_sitelinks == 0


def test_rule2_off_judges_each_spelling_on_its_own(session: Session, tmp_path: Path) -> None:
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(collapse_duplicates=False))

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Beethoven, Ludwig van" in labels  # evidence of its own
        # without collapsing, the encyclopedia-only spelling has no evidence
        assert "Beethoven" not in labels
    assert stats.duplicates_collapsed == 0
    assert stats.persons_dropped == 2  # Nobody, Obscure + the short spelling


def test_rules_1_and_2_off_keep_both_spellings(session: Session, tmp_path: Path) -> None:
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(
        session, gold_path, PromoteConfig(drop_unevidenced_persons=False, collapse_duplicates=False)
    )

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert {"Beethoven", "Beethoven, Ludwig van", "Nobody, Obscure"} <= labels
        # gold never carries canonical links, even with collapsing off
        assert gold.scalar(select(Entity.id).where(Entity.canonical_entity_id.is_not(None))) is None
    assert stats.persons_dropped == 0
    assert stats.duplicates_collapsed == 0
    assert stats.persons_kept == 4


def test_rule3_off_keeps_unreferenced_entities(session: Session, tmp_path: Path) -> None:
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(prune_unreferenced=False))

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Atlantis" in labels  # kept although only a dropped person referred to it
        assert "Nobody, Obscure" not in labels  # rule 1 still applies
        assert "Vienna" in labels  # referenced entities unaffected
    assert stats.entities_pruned == 0
    assert stats.persons_dropped == 1


def _seed_referrers_silver(session: Session) -> None:
    """Silver exercising rule 3's referrer threshold. All persons are kept
    (reported by a performance source), and they reference three places:

    - Popularville: two distinct persons refer to it (2 referrers),
    - Lonelyton: a single person refers to it (1 referrer),
    - Twiceville: one person refers to it via two claims (still 1 referrer).
    """
    archive = FakeSource(
        records=(
            mention("Symphony No. 5, Op. 67", "Composer, Evidence", "m1"),  # makes archive a perf source
            person("Popular, One", SourceClaim("born_in", "place", "Popularville"), external_id="a:one"),
            person("Popular, Two", SourceClaim("born_in", "place", "Popularville"), external_id="a:two"),
            person("Lonely, Composer", SourceClaim("born_in", "place", "Lonelyton"), external_id="a:lonely"),
            person(
                "Double, Claimer",
                SourceClaim("born_in", "place", "Twiceville"),
                SourceClaim("died_in", "place", "Twiceville"),
                external_id="a:double",
            ),
        ),
        name="archive",
        base_url="https://archive.example",
    )
    ingest_source(session, archive)


def test_min_referrers_default_keeps_single_referrer(session: Session, tmp_path: Path) -> None:
    _seed_referrers_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)  # default min_referrers=1

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert {"Popularville", "Lonelyton", "Twiceville"} <= labels  # one referrer suffices
    assert stats.entities_pruned == 0


def test_min_referrers_prunes_weakly_referenced(session: Session, tmp_path: Path) -> None:
    _seed_referrers_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(min_referrers=2))

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Popularville" in labels  # two distinct persons refer to it
        assert "Lonelyton" not in labels  # only one referrer
        assert "Twiceville" not in labels  # two claims but a single referrer
        # pruning an entity also drops the claims that pointed at it
        entity_ids = set(gold.scalars(select(Entity.id)))
        objects = set(gold.scalars(select(Claim.object_id).where(Claim.object_id.is_not(None))))
        assert objects <= entity_ids  # no claim points at a pruned row
    assert stats.entities_pruned >= 2  # Lonelyton + Twiceville


def test_min_referrers_keeps_shared_referrer_with_its_claims(session: Session, tmp_path: Path) -> None:
    _seed_referrers_silver(session)
    gold_path = tmp_path / "gold.db"
    promote(session, gold_path, PromoteConfig(min_referrers=2))

    with _gold_session(gold_path) as gold:
        popularville = gold.scalars(select(Entity).where(Entity.label == "Popularville")).one()
        claims = gold.scalars(
            select(Claim).where(Claim.object_id == popularville.id, Claim.predicate == "born_in")
        ).all()
        assert len(claims) == 2  # both referrers' claims ride along


def test_promote_writes_manifest_and_is_rerunnable(session: Session, tmp_path: Path) -> None:
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    first = promote(session, gold_path)
    second = promote(session, gold_path)  # full rebuild: same result, no leftovers

    assert first == second
    manifest = read_gold_manifest(gold_path)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.stats["persons_kept"] == 2
    assert not gold_path.with_suffix(".db.tmp").exists()
