"""Stable uuid ids for concerts and recordings

The derive passes used to number concerts and recordings 1..n in key order, so
one new concert shifted every id after it and broke ``/concerts/{id}`` links on
the next promote. They now key each row on ``event_uuid(kind, source name,
external key)``.

The six tables are derived data — ``derive-concerts`` and ``derive-recordings``
rebuild them from ``raw_work_mentions`` — so the upgrade empties them rather
than inventing a mapping from the old integers. Re-run both passes after
upgrading.

Revision ID: 4b262cdef2a5
Revises: 90dda614c67a
Create Date: 2026-10-23 17:22:02.682568

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b262cdef2a5"
down_revision: str | Sequence[str] | None = "90dda614c67a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (event table, its child tables, the child column pointing at it)
_EVENTS = (
    ("concerts", ("concert_participants", "concert_works"), "concert_id"),
    ("recordings", ("recording_participants", "recording_works"), "recording_id"),
)


def _empty() -> None:
    for table, children, _column in _EVENTS:
        for child in children:
            op.execute(f"DELETE FROM {child}")
        op.execute(f"DELETE FROM {table}")


def _drop_child_fks() -> None:
    for _table, children, column in _EVENTS:
        for child in children:
            op.drop_constraint(f"{child}_{column}_fkey", child, type_="foreignkey")


def _create_child_fks() -> None:
    for table, children, column in _EVENTS:
        for child in children:
            op.create_foreign_key(f"{child}_{column}_fkey", child, table, [column], ["id"])


def upgrade() -> None:
    """Upgrade schema."""
    _empty()
    _drop_child_fks()
    for table, children, column in _EVENTS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
        op.execute(f"DROP SEQUENCE {table}_id_seq")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id TYPE uuid USING NULL")
        for child in children:
            op.execute(f"ALTER TABLE {child} ALTER COLUMN {column} TYPE uuid USING NULL")
    _create_child_fks()


def downgrade() -> None:
    """Downgrade schema."""
    _empty()
    _drop_child_fks()
    for table, children, column in _EVENTS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id TYPE integer USING NULL")
        op.execute(f"CREATE SEQUENCE {table}_id_seq OWNED BY {table}.id")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{table}_id_seq')")
        for child in children:
            op.execute(f"ALTER TABLE {child} ALTER COLUMN {column} TYPE integer USING NULL")
    _create_child_fks()
