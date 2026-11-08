"""Add composer_programmes: concerts and recordings of each composer's works

Revision ID: 287ce11fcaf3
Revises: b4d4aa02f42f
Create Date: 2026-10-28 14:13:53.762408

The table is derived data, refreshed by ``derive_concerts``,
``derive_recordings`` and promote. It is backfilled here so a migrated
database lists composers before its next derive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "287ce11fcaf3"
down_revision: str | Sequence[str] | None = "b4d4aa02f42f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "composer_programmes",
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("concert_count", sa.Integer(), nullable=False),
        sa.Column("recording_count", sa.Integer(), nullable=False),
        sa.Column("programme_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("entity_id"),
    )
    op.create_index("ix_composer_programmes_concerts", "composer_programmes", ["concert_count"], unique=False)
    op.create_index(
        "ix_composer_programmes_recordings", "composer_programmes", ["recording_count"], unique=False
    )
    op.create_index("ix_composer_programmes_total", "composer_programmes", ["programme_count"], unique=False)
    op.execute(
        """
        INSERT INTO composer_programmes (entity_id, concert_count, recording_count, programme_count)
        SELECT entity_id, SUM(c)::integer, SUM(r)::integer, SUM(c + r)::integer
        FROM (
            SELECT m.composer_entity_id AS entity_id, COUNT(DISTINCT cw.concert_id) AS c, 0 AS r
            FROM concert_works cw JOIN raw_work_mentions m ON m.id = cw.mention_id
            WHERE m.composer_entity_id IS NOT NULL
            GROUP BY m.composer_entity_id
            UNION ALL
            SELECT m.composer_entity_id, 0, COUNT(DISTINCT rw.recording_id)
            FROM recording_works rw JOIN raw_work_mentions m ON m.id = rw.mention_id
            WHERE m.composer_entity_id IS NOT NULL
            GROUP BY m.composer_entity_id
        ) AS per_table
        GROUP BY entity_id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_composer_programmes_total", table_name="composer_programmes")
    op.drop_index("ix_composer_programmes_recordings", table_name="composer_programmes")
    op.drop_index("ix_composer_programmes_concerts", table_name="composer_programmes")
    op.drop_table("composer_programmes")
