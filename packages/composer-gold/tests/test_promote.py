# pylint: disable=too-many-lines
"""Tests for the silver → gold promote pipeline."""

from datetime import UTC, datetime
from pathlib import Path

from composer_gold import PromoteConfig, promote, read_gold_manifest
from composer_schema import EntityDocument, SourceClaim
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
from composer_warehouse.testing import (
    FakeSource,
    ensemble,
    ingest_source,
    mention,
    perf_mention,
    person,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def _seed_silver(session: Session) -> None:
    """Silver with every promote-relevant case:

    - Beethoven: mentioned as a composer of two work mentions (kept, rule 1a)
    - Mahler, Gustav: conducted a concert the archive reports (kept, rule 1b)
      with claims referencing Vienna (place kept via rule 3)
    - Nobody, Obscure: listed by a source but on no concert or recording
      (dropped, rule 1), referencing Atlantis (place pruned, rule 3)
    - "Beethoven": the same composer under a shorter spelling, listed by the
      encyclopedia only. Entity resolution keys on the normalized label, so it
      is its own entity, and with no credits of its own rule 1 drops it.
    """
    archive = FakeSource(
        records=(
            mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),
            mention("Sinfonie Nr. 5, op. 67", "Beethoven, Ludwig van", "m2"),
            perf_mention(
                "m3",
                "Symphony No. 5, Op. 67",
                "Beethoven, Ludwig van",
                {
                    "_source": "llm",
                    "concert_key": "https://archive.example/concert/1",
                    "date": "1910-01-02",
                    "venue": "Musikverein",
                    "conductors": ["Mahler, Gustav"],
                },
            ),
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

    derive_concerts(session)  # rule 1's evidence lives in the derived concert tables


def _gold_session(gold_path: Path) -> Session:
    return init_db(create_engine(f"sqlite:///{gold_path}"))()


def test_promote_applies_all_three_rules(session: Session, tmp_path: Path) -> None:
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        # rule 1: kept via mention (Beethoven) and via a concert credit (Mahler)
        assert "Beethoven, Ludwig van" in labels
        assert "Mahler, Gustav" in labels
        assert "Nobody, Obscure" not in labels  # listed nowhere but an index: dropped
        assert "Beethoven" not in labels  # the short spelling has no credits of its own
        # rule 3: place referenced by a kept person survives, the orphan doesn't
        assert "Vienna" in labels
        assert "Atlantis" not in labels
        # only dropped persons asserted a profession, so its object is pruned too
        assert "composer" not in labels

    assert stats.persons_kept == 2
    assert stats.persons_dropped == 2  # Nobody, Obscure and the short "Beethoven"
    assert stats.entities_pruned >= 1  # Atlantis


def test_promote_copies_works_and_mentions_with_their_composer(session: Session, tmp_path: Path) -> None:
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        beethoven = gold.scalars(select(Entity).where(Entity.label == "Beethoven, Ludwig van")).one()
        # works and mentions resolve to the composer entity gold kept
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
        assert by_role["conductor"].entity_id == rattle.id
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


def test_promote_nulls_participant_links_to_dropped_persons(session: Session, tmp_path: Path) -> None:
    """A participant resolved to a person gold dropped keeps the verbatim name
    but loses the link; a kept one keeps both.

    The threshold is raised to two appearances so the one-off conductor is the
    dropped person: Beinum conducts both Amsterdam evenings, the ghost one.
    """
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
                {"date": "01-07-1929", "city": "Amsterdam", "conductor": "Beinum, Eduard van"},
            ),
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
            person("Ghost, Dropped", external_id="nyp:ghost"),
        ),
        name="nyphil",
        base_url="https://nyp.example",
    )
    for source in (concertgebouw, nyphil):
        ingest_source(session, source)

    beinum = session.scalars(select(Entity).where(Entity.label == "Beinum, Eduard van")).one()
    derive_concerts(session)

    stats = promote(session, tmp_path / "gold.db", PromoteConfig(min_appearances=2))

    with _gold_session(tmp_path / "gold.db") as gold:
        conductors = gold.scalars(
            select(ConcertParticipant).where(ConcertParticipant.name == "Beinum, Eduard van")
        ).all()
        assert {c.entity_id for c in conductors} == {beinum.id}  # kept person: link intact
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
        # both spellings stand on their own
        assert "Beethoven" in labels
    assert stats.persons_dropped == 0
    assert stats.persons_kept == 4  # both Beethoven spellings, Mahler, Nobody
    assert stats.persons_promoted_by_sitelinks == 0


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
    assert stats.persons_dropped == 2


def _seed_referrers_silver(session: Session) -> None:
    """Silver exercising rule 3's referrer threshold. All persons are kept
    (they conduct the archive's concert), and they reference three places:

    - Popularville: two distinct persons refer to it (2 referrers),
    - Lonelyton: a single person refers to it (1 referrer),
    - Twiceville: one person refers to it via two claims (still 1 referrer).
    """
    archive = FakeSource(
        records=(
            perf_mention(
                "m1",
                "Symphony No. 5, Op. 67",
                "Composer, Evidence",
                {
                    "_source": "llm",
                    "concert_key": "https://archive.example/concert/1",
                    "date": "1929-06-30",
                    "conductors": [
                        "Popular, One",
                        "Popular, Two",
                        "Lonely, Composer",
                        "Double, Claimer",
                    ],
                },
            ),
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
    derive_concerts(session)


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


def test_rule1_drops_archive_listed_person_without_credits(session: Session, tmp_path: Path) -> None:
    """Appearing in a performance source's artist index is not evidence — only a
    credit on a concert or a recording is."""
    archive = FakeSource(
        records=(
            perf_mention(
                "perf:10-1",
                "Paradise and the Peri",
                "Robert Schumann",
                {
                    "concert_id": "10",
                    "date": "2009-02-08",
                    "conductors": ["Sir Simon Rattle"],
                },
            ),
            person("Rattle, Sir Simon", external_id="bp:rattle"),
            person("Repetiteur, Uncredited", external_id="bp:repetiteur"),  # index entry only
        ),
        name="berlinphil",
        base_url="https://bp.example",
    )
    ingest_source(session, archive)
    derive_concerts(session)

    stats = promote(session, tmp_path / "gold.db")

    with _gold_session(tmp_path / "gold.db") as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Rattle, Sir Simon" in labels  # conducted the concert
        assert "Repetiteur, Uncredited" not in labels  # listed by the archive, credited nowhere
    assert stats.persons_kept_by_appearances == 1


def test_rule1_keeps_recording_participants(session: Session, tmp_path: Path) -> None:
    _seed_recording_silver(session)
    derive_recordings(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert {"Simon Rattle", "Janine Jansen"} <= labels  # credited on the album
    assert stats.persons_kept_by_appearances == 2


def test_min_appearances_drops_one_off_participants(session: Session, tmp_path: Path) -> None:
    """A higher threshold keeps only the recurring musicians; composers are
    evidenced by their works, so the threshold never touches them."""
    _seed_concert_silver(session)
    derive_concerts(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(min_appearances=2))

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        # every performer here is credited on exactly one concert
        assert "Beinum, Eduard van" not in labels
        assert "Mengelberg, Willem" not in labels
        assert "Matthews, Sally" not in labels
        assert {"Beethoven, Ludwig van", "Verdi, Giuseppe"} <= labels  # composers of the programme
    assert stats.persons_kept_by_appearances == 0


def _seed_ensemble_silver(session: Session) -> None:
    """Silver where one orchestra plays a concert and another only exists in the
    source's ensemble index (referenced by a kept person's claim)."""
    berlinphil = FakeSource(
        records=(
            perf_mention(
                "perf:10-1",
                "Paradise and the Peri",
                "Robert Schumann",
                {
                    "concert_id": "10",
                    "date": "2009-02-08",
                    "conductors": ["Sir Simon Rattle"],
                    "ensembles": ["Berliner Philharmoniker"],
                },
            ),
            person(
                "Rattle, Sir Simon",
                SourceClaim("member_of", "ensemble", "Guest Orchestra"),
                external_id="bp:rattle",
            ),
            ensemble("Berliner Philharmoniker", external_id="bp:ens-berlin"),
            ensemble("Guest Orchestra", external_id="bp:ens-guest"),
        ),
        name="berlinphil",
        base_url="https://bp.example",
    )
    ingest_source(session, berlinphil)
    derive_concerts(session)


def test_ensembles_need_a_credit_of_their_own(session: Session, tmp_path: Path) -> None:
    _seed_ensemble_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert "Berliner Philharmoniker" in labels  # played the concert
        assert "Guest Orchestra" not in labels  # only referenced by a kept person's claim
        # the participant row keeps the verbatim name and links to the ensemble
        orchestra = gold.scalars(select(Entity).where(Entity.label == "Berliner Philharmoniker")).one()
        participant = gold.scalars(
            select(ConcertParticipant).where(ConcertParticipant.role == "ensemble")
        ).one()
        assert participant.entity_id == orchestra.id
        # no claim is left pointing at the pruned ensemble
        objects = set(gold.scalars(select(Claim.object_id).where(Claim.object_id.is_not(None))))
        assert objects <= set(gold.scalars(select(Entity.id)))
    assert (stats.ensembles_kept, stats.ensembles_dropped) == (1, 1)


def test_rule1_off_keeps_unevidenced_ensembles(session: Session, tmp_path: Path) -> None:
    _seed_ensemble_silver(session)
    gold_path = tmp_path / "gold.db"
    stats = promote(session, gold_path, PromoteConfig(drop_unevidenced_persons=False))

    with _gold_session(gold_path) as gold:
        labels = {e.label for e in gold.scalars(select(Entity))}
        assert {"Berliner Philharmoniker", "Guest Orchestra"} <= labels
    assert (stats.ensembles_kept, stats.ensembles_dropped) == (2, 0)


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


def _work_entity(label: str, *claims: SourceClaim) -> EntityDocument:
    """A ``work`` entity as the claims extractor emits one, carrying the facts a
    work page stated about it."""
    return EntityDocument(
        id=f"work:{label}",
        url="https://www.laphil.com/works/violin-concerto-beethoven",
        source_name="laphil",
        ingested_at=datetime(2024, 5, 1, tzinfo=UTC),
        name=label,
        kind="work",
        claims=claims,
    )


_WORK_LABEL = "Beethoven, Ludwig van: Violin Concerto"


def _seed_work_claims(session: Session, *, attributed: bool) -> None:
    """Silver as a crawled work page leaves it: a kept composer, a work entity
    carrying the page's facts, and — when *attributed* — the ``composed`` edge
    between them."""
    composer_claims = (SourceClaim("composed", "work", _WORK_LABEL),) if attributed else ()
    laphil = FakeSource(
        records=(
            mention("Violin Concerto", "Beethoven, Ludwig van", "lp:m1"),
            person("Beethoven, Ludwig van", *composer_claims, external_id="lp:beethoven"),
            _work_entity(
                _WORK_LABEL,
                SourceClaim("composed_in", value="1806"),
                SourceClaim("duration_minutes", value="42"),
                SourceClaim("program_note_by", value="Hugh Macdonald"),
            ),
        ),
        name="laphil",
        base_url="https://www.laphil.com",
    )
    ingest_source(session, laphil)


def test_promote_keeps_work_claims_reached_through_the_composed_edge(
    session: Session, tmp_path: Path
) -> None:
    """The whole point of emitting ``composed``: gold seeds its walk from the
    claims of kept persons, so a work entity is only copied — and only brings its
    own literal claims with it — because a kept composer points at it."""
    _seed_work_claims(session, attributed=True)
    gold_path = tmp_path / "gold.db"
    promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        work = gold.scalars(select(Entity).where(Entity.kind == "work")).one()
        assert work.label == _WORK_LABEL
        facts = {
            (c.predicate, c.value) for c in gold.scalars(select(Claim).where(Claim.subject_id == work.id))
        }
        assert {
            ("composed_in", "1806"),
            ("duration_minutes", "42"),
            ("program_note_by", "Hugh Macdonald"),
        } <= facts


def test_promote_drops_an_unattributed_work_entity(session: Session, tmp_path: Path) -> None:
    """The same page without the ``composed`` edge: nothing kept references the
    work, so rule 3 prunes it and every fact the page stated is lost."""
    _seed_work_claims(session, attributed=False)
    gold_path = tmp_path / "gold.db"
    promote(session, gold_path)

    with _gold_session(gold_path) as gold:
        assert gold.scalars(select(Entity).where(Entity.kind == "work")).all() == []
        assert gold.scalar(select(Claim).where(Claim.predicate == "composed_in")) is None
