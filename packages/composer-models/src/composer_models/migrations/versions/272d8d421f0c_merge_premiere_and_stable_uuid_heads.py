"""Merge the premiere and stable uuid heads

#342 (premiere place and source) and #343 (stable uuid ids for concerts and
recordings) each added a revision on top of 90dda614c67a. They touch different
tables, so the merge has nothing to do beyond joining the two branches.

Revision ID: 272d8d421f0c
Revises: 4b262cdef2a5, afeb910474b6
Create Date: 2026-10-24 10:23:19.050399

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "272d8d421f0c"
down_revision: str | Sequence[str] | None = ("4b262cdef2a5", "afeb910474b6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
