"""add durable WhatsApp inbound inbox

Revision ID: d6f14a0e9c32
Revises: be64f1a0192c
Create Date: 2026-11-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d6f14a0e9c32"
down_revision: str | Sequence[str] | None = "be64f1a0192c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


inbound_event_type = postgresql.ENUM(
    "MESSAGE",
    "STATUS_CALLBACK",
    name="inbound_event_type",
    create_type=False,
)
inbound_event_status = postgresql.ENUM(
    "PENDING",
    "PROCESSING",
    "RETRY_SCHEDULED",
    "COMPLETED",
    "FAILED",
    name="inbound_event_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    inbound_event_type.create(bind, checkfirst=True)
    inbound_event_status.create(bind, checkfirst=True)

    op.create_table(
        "inbound_whatsapp_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", inbound_event_type, nullable=False),
        sa.Column("status", inbound_event_status, nullable=False),
        sa.Column("whatsapp_message_id", sa.String(length=255), nullable=True),
        sa.Column("sender_identifier", sa.String(length=255), nullable=True),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_payload", sa.JSON(), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_inbound_whatsapp_events_event_key"),
    )
    op.create_index(
        "uq_inbound_whatsapp_events_message_id",
        "inbound_whatsapp_events",
        ["whatsapp_message_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'MESSAGE'"),
    )
    op.create_index(
        "ix_inbound_whatsapp_events_status_available",
        "inbound_whatsapp_events",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_inbound_whatsapp_events_status_lease",
        "inbound_whatsapp_events",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM inbound_whatsapp_events) THEN
                RAISE EXCEPTION
                    'Cannot downgrade while durable inbound event data exists';
            END IF;
        END
        $$
        """
    )
    op.drop_index(
        "ix_inbound_whatsapp_events_status_lease",
        table_name="inbound_whatsapp_events",
    )
    op.drop_index(
        "ix_inbound_whatsapp_events_status_available",
        table_name="inbound_whatsapp_events",
    )
    op.drop_index(
        "uq_inbound_whatsapp_events_message_id",
        table_name="inbound_whatsapp_events",
    )
    op.drop_table("inbound_whatsapp_events")
    inbound_event_status.drop(op.get_bind(), checkfirst=True)
    inbound_event_type.drop(op.get_bind(), checkfirst=True)
