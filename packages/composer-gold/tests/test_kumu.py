"""Tests for the gold → Kumu blueprint export."""

import json
from pathlib import Path
from typing import Any

from composer_gold import KumuConfig, build_blueprint, export_kumu, promote
from composer_models.db import init_db
from composer_schema import SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.testing import FakeSource, ingest_source, perf_mention, person
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _concert(key: str, date: str, conductor: str, soloist: str | None = None) -> dict[str, Any]:
    """An LLM-extract concert payload — the source-independent shape
    ``derive_concerts`` reads regardless of which site a mention was crawled
    from (see composer_warehouse.concerts.payloads)."""
    payload: dict[str, Any] = {
        "_source": "llm",
        "concert_key": key,
        "date": date,
        "venue": "Concertgebouw",
        "conductors": [conductor],
    }
    if soloist:
        payload["soloists"] = [{"name": soloist, "discipline": "violin"}]
    return payload


def _seed_silver(session: Session) -> None:
    """Two conductors sharing one composer, plus a soloist on one concert.

    Beinum conducts two Beethoven works on one night and a Schumann on another;
    Mengelberg conducts one Beethoven. That gives an edge of weight 2 and three
    of weight 1, which is what the ``min_weight`` cut is measured against.
    """
    archive = FakeSource(
        records=(
            perf_mention(
                "perf:1",
                "Symphony No. 5, Op. 67",
                "Beethoven, Ludwig van",
                _concert("c1", "1929-06-30", "Beinum, Eduard van"),
            ),
            perf_mention(
                "perf:2",
                "Egmont Overture, Op. 84",
                "Beethoven, Ludwig van",
                _concert("c1", "1929-06-30", "Beinum, Eduard van"),
            ),
            perf_mention(
                "perf:3",
                "Symphony No. 3, Op. 97",
                "Schumann, Robert",
                _concert("c2", "1930-01-05", "Beinum, Eduard van"),
            ),
            perf_mention(
                "perf:4",
                "Violin Concerto, Op. 61",
                "Beethoven, Ludwig van",
                _concert("c3", "1929-07-01", "Mengelberg, Willem", soloist="Zimmermann, Louis"),
            ),
            person(
                "Beinum, Eduard van",
                SourceClaim("born_in", "place", "Arnhem"),
                SourceClaim("has_profession", "profession", "conductor"),
                SourceClaim("born_on", value="1900-09-03"),
                SourceClaim("performs_as", value="Piano"),
                SourceClaim("performs_as", value="piano"),
                SourceClaim("program_count", value="12"),
                external_id="a:beinum",
            ),
            person("Mengelberg, Willem", external_id="a:mengelberg"),
            person("Zimmermann, Louis", external_id="a:zimmermann"),
        ),
        name="archive",
        base_url="https://archive.example",
    )
    ingest_source(session, archive)
    derive_concerts(session)
    session.commit()


def _gold(session: Session, tmp_path: Path) -> Session:
    """Promote the seeded silver and open a session on the resulting gold."""
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    promote(session, gold_path)
    return init_db(create_engine(f"sqlite:///{gold_path}"))()


def _by_label(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {element["label"]: element for element in elements}


def _edges(connections: list[dict[str, Any]], elements: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    """Connections as (from label, type, to label), which is what the assertions
    are actually about — the ids are UUIDs."""
    labels = {element["id"]: element["label"] for element in elements}
    return {
        (labels[connection["from"]], connection["type"], labels[connection["to"]])
        for connection in connections
    }


def test_blueprint_maps_performers_to_the_composers_they_programmed(session: Session, tmp_path: Path) -> None:
    with _gold(session, tmp_path) as gold:
        blueprint = build_blueprint(gold)

    edges = _edges(blueprint.connections, blueprint.elements)
    assert ("Beinum, Eduard van", "performed", "Beethoven, Ludwig van") in edges
    assert ("Beinum, Eduard van", "performed", "Schumann, Robert") in edges
    assert ("Mengelberg, Willem", "performed", "Beethoven, Ludwig van") in edges
    # Weighted by performances, not by concerts: two Beethoven works on one night.
    weights = {
        connection["Performances"]
        for connection in blueprint.connections
        if connection["type"] == "performed" and connection["Performances"] > 1
    }
    assert weights == {2}
    # Soloists count as performers too: Zimmermann → Beethoven is the fourth.
    assert ("Zimmermann, Louis", "performed", "Beethoven, Ludwig van") in edges
    assert blueprint.stats.performance_edges == 4


def test_blueprint_carries_claims_as_connections_and_literals_as_fields(
    session: Session, tmp_path: Path
) -> None:
    with _gold(session, tmp_path) as gold:
        blueprint = build_blueprint(gold)

    edges = _edges(blueprint.connections, blueprint.elements)
    assert ("Beinum, Eduard van", "born in", "Arnhem") in edges
    assert ("Beinum, Eduard van", "has profession", "conductor") in edges

    elements = _by_label(blueprint.elements)
    beinum = elements["Beinum, Eduard van"]
    assert beinum["type"] == "Person"
    assert beinum["tags"] == ["conductor"]
    assert beinum["Born"] == "1900-09-03"  # a date stays a date
    assert beinum["Programs"] == 12  # a count becomes a number Kumu can size by
    assert beinum["Instruments"] == "Piano"  # "piano" is the same instrument
    assert beinum["Concerts"] == 2
    assert beinum["Sources"] == "archive"
    assert "1900" in beinum["description"]

    # Claim objects are elements in their own right, typed by entity kind.
    assert elements["Arnhem"]["type"] == "Place"
    assert elements["conductor"]["type"] == "Profession"


def test_composers_are_tagged_even_when_they_never_performed(session: Session, tmp_path: Path) -> None:
    with _gold(session, tmp_path) as gold:
        blueprint = build_blueprint(gold)

    beethoven = _by_label(blueprint.elements)["Beethoven, Ludwig van"]
    assert beethoven["tags"] == ["composer"]
    assert "Concerts" not in beethoven  # credited on no concert of his own


def test_min_weight_drops_the_thinly_evidenced_pairings(session: Session, tmp_path: Path) -> None:
    with _gold(session, tmp_path) as gold:
        blueprint = build_blueprint(gold, KumuConfig(min_weight=2))

    edges = _edges(blueprint.connections, blueprint.elements)
    assert ("Beinum, Eduard van", "performed", "Beethoven, Ludwig van") in edges
    assert ("Beinum, Eduard van", "performed", "Schumann, Robert") not in edges
    # Schumann loses his only performance edge and has no claims: off the map.
    assert "Schumann, Robert" not in _by_label(blueprint.elements)
    # Mengelberg's one edge goes too, and he has no claims of his own either.
    assert "Mengelberg, Willem" not in _by_label(blueprint.elements)


def test_performer_limit_cuts_the_ranking_at_the_top(session: Session, tmp_path: Path) -> None:
    with _gold(session, tmp_path) as gold:
        blueprint = build_blueprint(gold, KumuConfig(performer_limit=1))

    # Beinum (three credits) outranks Mengelberg (one), so only his side survives.
    labels = _by_label(blueprint.elements)
    assert "Beinum, Eduard van" in labels
    assert "Mengelberg, Willem" not in labels
    assert blueprint.stats.performers == 1


def test_claims_can_be_switched_off(session: Session, tmp_path: Path) -> None:
    with _gold(session, tmp_path) as gold:
        blueprint = build_blueprint(gold, KumuConfig(claims=False))

    assert blueprint.stats.claim_edges == 0
    assert {connection["type"] for connection in blueprint.connections} == {"performed"}
    assert "Arnhem" not in _by_label(blueprint.elements)


def test_performances_can_be_switched_off(session: Session, tmp_path: Path) -> None:
    with _gold(session, tmp_path) as gold:
        blueprint = build_blueprint(gold, KumuConfig(performances=False))

    assert blueprint.stats.performance_edges == 0
    # Without the performance pass nothing pulls composers in, only claim edges.
    assert "Beethoven, Ludwig van" not in _by_label(blueprint.elements)
    assert ("Beinum, Eduard van", "born in", "Arnhem") in _edges(blueprint.connections, blueprint.elements)


def test_export_writes_the_two_arrays_kumu_expects(session: Session, tmp_path: Path) -> None:
    out = tmp_path / "kumu.json"
    with _gold(session, tmp_path) as gold:
        stats = export_kumu(gold, out)

    written = json.loads(out.read_text(encoding="utf-8"))
    assert set(written) == {"elements", "connections"}
    assert len(written["elements"]) == stats.elements
    assert len(written["connections"]) == stats.connections
    # Every connection endpoint resolves to an element Kumu will have seen.
    ids = {element["id"] for element in written["elements"]}
    for connection in written["connections"]:
        assert connection["from"] in ids and connection["to"] in ids
        assert connection["direction"] == "directed"


def test_export_is_reproducible(session: Session, tmp_path: Path) -> None:
    with _gold(session, tmp_path) as gold:
        first = build_blueprint(gold)
        second = build_blueprint(gold)
    assert first.to_dict() == second.to_dict()
