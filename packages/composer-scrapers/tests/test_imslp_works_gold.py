"""Tests for the gold.db composer query ``imslp_works`` is scoped to."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from composer_scrapers.imslp_works.gold import GoldComposer, composers

_SCHEMA = """
create table entities (id text primary key, kind text, label text);
create table claims (subject_id text, predicate text, object_id text);
create table sources (id integer primary key, name text);
create table entity_records (source_id integer, entity_id text, url text);
"""


@pytest.fixture
def gold_db(tmp_path: Path) -> Iterator[str]:
    db_path = tmp_path / "gold.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)

    conn.execute("insert into entities values ('c1', 'person', 'Beethoven, Ludwig van')")
    conn.execute("insert into entities values ('c2', 'person', 'Bartok, Bela')")
    conn.execute("insert into entities values ('c3', 'person', 'Karajan, Herbert von')")  # conductor only
    conn.execute("insert into entities values ('prof1', 'profession', 'composer')")
    conn.execute("insert into entities values ('prof2', 'profession', 'composer, conductor')")
    conn.execute("insert into entities values ('prof3', 'profession', 'conductor')")
    conn.execute("insert into claims values ('c1', 'has_profession', 'prof1')")
    conn.execute("insert into claims values ('c2', 'has_profession', 'prof2')")
    conn.execute("insert into claims values ('c3', 'has_profession', 'prof3')")
    conn.execute("insert into sources values (1, 'imslp')")
    conn.execute("insert into sources values (2, 'wikidata')")
    conn.execute(
        "insert into entity_records values (1, 'c1', 'https://imslp.org/wiki/Category:Beethoven,_Ludwig_van')"
    )
    conn.execute("insert into entity_records values (2, 'c1', 'https://www.wikidata.org/wiki/Q255')")
    conn.commit()
    conn.close()
    yield str(db_path)


def test_composers_includes_only_composer_professed_persons(gold_db: str) -> None:
    result = composers(gold_db)
    assert {c.entity_id for c in result} == {"c1", "c2"}


def test_composers_matches_compound_profession_labels(gold_db: str) -> None:
    """ "composer, conductor" still counts as a composer."""
    result = {c.entity_id: c for c in composers(gold_db)}
    assert "c2" in result


def test_composers_carries_the_known_imslp_url(gold_db: str) -> None:
    result = {c.entity_id: c for c in composers(gold_db)}
    assert result["c1"].known_imslp_url == "https://imslp.org/wiki/Category:Beethoven,_Ludwig_van"


def test_composers_leaves_unlinked_composers_with_no_url(gold_db: str) -> None:
    result = {c.entity_id: c for c in composers(gold_db)}
    assert result["c2"].known_imslp_url is None


def test_composers_returns_dataclass_instances(gold_db: str) -> None:
    result = composers(gold_db)
    assert all(isinstance(c, GoldComposer) for c in result)
