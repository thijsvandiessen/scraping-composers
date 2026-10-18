"""A small gold database to build the graph from: two conductors, a soloist,
two composers and the concerts that tie them together."""

from pathlib import Path
from typing import Any

from composer_gold import promote
from composer_models.db import init_db
from composer_schema import SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.testing import FakeSource, ingest_source, perf_mention, person
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _concert(key: str, date: str, conductor: str, soloist: str | None = None) -> dict[str, Any]:
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
    """Beinum conducts Beethoven and Schumann on one night (c1) and Schumann
    again on another (c2); Mengelberg conducts Beethoven with Zimmermann as
    soloist (c3). So Beethoven and Schumann share one concert, and Beinum
    performed Schumann's symphony at two."""
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
                "Symphony No. 3, Op. 97",
                "Schumann, Robert",
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
                SourceClaim("statement_count", value="40"),
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


def gold_session(session: Session, tmp_path: Path) -> Session:
    """Promote the seeded silver and open a session on the resulting gold."""
    _seed_silver(session)
    gold_path = tmp_path / "gold.db"
    promote(session, f"sqlite:///{gold_path}")
    return init_db(create_engine(f"sqlite:///{gold_path}"))()
