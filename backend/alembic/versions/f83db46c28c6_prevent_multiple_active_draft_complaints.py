"""prevent multiple active draft complaints

Revision ID: f83db46c28c6
Revises: d25fde604a81
Create Date: 2026-11-22 19:11:47.560910

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f83db46c28c6'
down_revision: Union[str, Sequence[str], None] = 'd25fde604a81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    """Ensure each user has at most one active draft complaint."""
    op.create_index(
        "uq_complaints_one_draft_per_user",
        "complaints",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )


def downgrade() -> None:
    """Remove the active draft uniqueness constraint."""
    op.drop_index(
        "uq_complaints_one_draft_per_user",
        table_name="complaints",
    )

