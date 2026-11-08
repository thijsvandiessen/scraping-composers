"""add work identifiers and work links

Revision ID: b4d4aa02f42f
Revises: 272d8d421f0c
Create Date: 2026-10-24 10:00:07.901519

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d4aa02f42f"
down_revision: str | Sequence[str] | None = "272d8d421f0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "work_identifiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("scheme", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_id"],
            ["works.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", "scheme", "value", "source_id", name="uq_work_identifier"),
    )
    op.create_index("ix_work_identifiers_scheme_value", "work_identifiers", ["scheme", "value"], unique=False)
    op.create_index("ix_work_identifiers_work", "work_identifiers", ["work_id"], unique=False)
    op.create_table(
        "work_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=20), nullable=False),
        sa.Column("target_work_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_work_id"],
            ["works.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_id"],
            ["works.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", "relation", "target_work_id", name="uq_work_link"),
    )
    op.create_index("ix_work_links_target", "work_links", ["target_work_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_work_links_target", table_name="work_links")
    op.drop_table("work_links")
    op.drop_index("ix_work_identifiers_work", table_name="work_identifiers")
    op.drop_index("ix_work_identifiers_scheme_value", table_name="work_identifiers")
    op.drop_table("work_identifiers")
