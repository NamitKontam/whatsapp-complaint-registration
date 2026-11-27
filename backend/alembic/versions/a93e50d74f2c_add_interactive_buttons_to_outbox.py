"""add interactive buttons to whatsapp outbox

Revision ID: a93e50d74f2c
Revises: ad4a68d40598
Create Date: 2026-11-26 11:12:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a93e50d74f2c"
down_revision: str | Sequence[str] | None = "ad4a68d40598"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbound_whatsapp_messages",
        sa.Column("interactive_buttons", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(
        "outbound_whatsapp_messages",
        "interactive_buttons",
    )
