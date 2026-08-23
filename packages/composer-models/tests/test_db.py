"""The engine/session helpers and the schema they create."""

import pytest
from composer_models import Base, Entity
from composer_models.db import get_engine, init_db
from composer_models.normalize import MAX_KEY_CHARS, dedup_key, entity_uuid
from sqlalchemy import UniqueConstraint, inspect


def test_init_db_creates_every_table_of_the_shared_schema() -> None:
    engine = get_engine("sqlite://")
    init_db(engine)
    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables)


def test_session_round_trips_an_entity() -> None:
    factory = init_db(get_engine("sqlite://"))
    name = "Beethoven, Ludwig van"
    key = dedup_key(name)
    with factory() as session:
        session.add(Entity(id=entity_uuid("person", key), kind="person", dedup_key=key, label=name))
        session.commit()
    with factory() as session:
        stored = session.query(Entity).one()
        assert stored.label == name
        assert stored.id == entity_uuid("person", key)


# Columns fed by scrapers or LLM extraction, where no length bound is
# trustworthy: on SQLite a String(n) is advisory, but Postgres enforces it and
# an over-long value aborts the whole rebuild. Production data has already
# exceeded the old limits on entities.label (1991 chars), entity_records.name,
# recordings.format and concert_participants.discipline.
UNBOUNDED_COLUMNS = [
    ("entities", "dedup_key"),
    ("entities", "label"),
    ("entity_records", "external_id"),
    ("entity_records", "name"),
    ("works", "canonical_title"),
    ("works", "title_key"),
    ("work_titles", "title_key"),
    ("raw_work_mentions", "raw_title"),
    ("concerts", "event_type"),
    ("concert_participants", "discipline"),
    ("recordings", "catalogue_number"),
    ("recordings", "format"),
]


@pytest.mark.parametrize(("table", "column"), UNBOUNDED_COLUMNS)
def test_scraper_sourced_columns_carry_no_length_limit(table: str, column: str) -> None:
    assert getattr(Base.metadata.tables[table].c[column].type, "length", None) is None


@pytest.mark.parametrize(("table", "column"), [("entities", "dedup_key"), ("work_titles", "title_key")])
def test_key_columns_sit_in_a_unique_constraint(table: str, column: str) -> None:
    """These are the columns whose *values* the normalizers must bound: they
    are unbounded in the schema but live in a btree index Postgres caps."""
    constrained = {
        col.name
        for constraint in Base.metadata.tables[table].constraints
        if isinstance(constraint, UniqueConstraint)
        for col in constraint.columns
    }
    assert column in constrained


def test_the_key_bound_fits_a_postgres_index_tuple() -> None:
    # Postgres caps a btree index tuple at ~2704 bytes; UTF-8 is at most 4
    # bytes per character, so this is the worst case for a truncated key.
    assert MAX_KEY_CHARS * 4 < 2704


def test_get_engine_rejects_a_schema_name_that_cannot_be_escaped() -> None:
    # The name is interpolated into DDL (schema names can't be bound), so it
    # is validated rather than escaped.
    with pytest.raises(ValueError, match="invalid schema name"):
        get_engine("postgresql+psycopg://u:p@h/db", schema='x"; drop schema public cascade; --')


def test_get_engine_ignores_the_schema_for_sqlite() -> None:
    assert get_engine("sqlite://", schema="silver").dialect.name == "sqlite"
