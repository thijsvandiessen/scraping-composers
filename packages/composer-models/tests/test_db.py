"""The engine/session helpers and the schema they create."""

from composer_models import Base, Entity
from composer_models.db import get_engine, init_db
from composer_models.normalize import dedup_key, entity_uuid
from sqlalchemy import inspect


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
