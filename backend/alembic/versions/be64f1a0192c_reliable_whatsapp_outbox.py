"""add bounded retries and pending WhatsApp status reconciliation

Revision ID: be64f1a0192c
Revises: c84b0da3f6e1
Create Date: 2026-11-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "be64f1a0192c"
down_revision: str | Sequence[str] | None = "c84b0da3f6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE outbound_message_status ADD VALUE IF NOT EXISTS 'RETRY_SCHEDULED'"
    )
    op.execute("ALTER TYPE outbound_message_status ADD VALUE IF NOT EXISTS 'UNCERTAIN'")

    op.add_column(
        "outbound_whatsapp_messages",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbound_whatsapp_messages",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbound_whatsapp_messages",
        sa.Column("provider_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "outbound_whatsapp_messages",
        sa.Column(
            "provider_status_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_outbound_whatsapp_messages_status_next_attempt_at",
        "outbound_whatsapp_messages",
        ["status", "next_attempt_at"],
        unique=False,
    )

    op.create_table(
        "pending_whatsapp_statuses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("whatsapp_message_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider_timestamp", sa.String(length=50), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "whatsapp_message_id",
            "status",
            name="uq_pending_whatsapp_status_message_status",
        ),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM outbound_whatsapp_messages
                WHERE status IN ('RETRY_SCHEDULED', 'UNCERTAIN')
                   OR (status = 'PENDING' AND attempts > 0)
                   OR next_attempt_at IS NOT NULL
                   OR last_attempt_at IS NOT NULL
                   OR provider_status IS NOT NULL
                   OR provider_status_timestamp IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM pending_whatsapp_statuses
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade while retry or delivery reconciliation data exists';
            END IF;
        END
        $$
        """
    )

    op.drop_table("pending_whatsapp_statuses")
    op.drop_index(
        "ix_outbound_whatsapp_messages_status_next_attempt_at",
        table_name="outbound_whatsapp_messages",
    )
    op.drop_column("outbound_whatsapp_messages", "provider_status_timestamp")
    op.drop_column("outbound_whatsapp_messages", "provider_status")
    op.drop_column("outbound_whatsapp_messages", "last_attempt_at")
    op.drop_column("outbound_whatsapp_messages", "next_attempt_at")

    op.execute(
        """
        ALTER TYPE outbound_message_status
        RENAME TO outbound_message_status_with_retries
        """
    )
    op.execute(
        "CREATE TYPE outbound_message_status AS ENUM ('PENDING', 'SENT', 'FAILED')"
    )
    op.execute(
        """
        ALTER TABLE outbound_whatsapp_messages
        ALTER COLUMN status TYPE outbound_message_status
        USING status::text::outbound_message_status
        """
    )
    op.execute("DROP TYPE outbound_message_status_with_retries")
