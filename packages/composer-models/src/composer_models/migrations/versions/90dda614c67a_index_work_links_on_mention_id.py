"""Index concert_works and recording_works on mention_id

Revision ID: 90dda614c67a
Revises: 4fa913d6b458
Create Date: 2026-09-21 23:05:05.807600

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "90dda614c67a"
down_revision: str | Sequence[str] | None = "4fa913d6b458"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_concert_works_mention", "concert_works", ["mention_id"], unique=False)
    op.create_index("ix_recording_works_mention", "recording_works", ["mention_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_recording_works_mention", table_name="recording_works")
    op.drop_index("ix_concert_works_mention", table_name="concert_works")
