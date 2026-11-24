"""add complaint number sequence

Revision ID: 7392a21f3eaf
Revises: f83db46c28c6
Create Date: 2026-11-24 09:06:55.892687

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7392a21f3eaf'
down_revision: str | Sequence[str] | None = 'f83db46c28c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sequence for generating complaint reference numbers."""
    op.execute(
        """
        CREATE SEQUENCE complaint_number_seq
        START WITH 1
        INCREMENT BY 1
        NO CYCLE
        """
    )


def downgrade() -> None:
    """Remove complaint number sequence."""
    op.execute("DROP SEQUENCE complaint_number_seq")