"""add unlinked unsupported inbox messages

Revision ID: c84b0da3f6e1
Revises: b6c4d93e2a11
Create Date: 2026-11-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c84b0da3f6e1"
down_revision: str | Sequence[str] | None = "b6c4d93e2a11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unsupported_whatsapp_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("whatsapp_message_id", sa.String(length=255), nullable=False),
        sa.Column("raw_type", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("whatsapp_message_id"),
    )
    op.create_index(
        op.f("ix_unsupported_whatsapp_messages_user_id"),
        "unsupported_whatsapp_messages",
        ["user_id"],
        unique=False,
    )

    op.alter_column(
        "outbound_whatsapp_messages",
        "inbound_message_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        "outbound_whatsapp_messages",
        "complaint_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.add_column(
        "outbound_whatsapp_messages",
        sa.Column("unsupported_inbound_message_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_outbound_whatsapp_messages_unsupported_inbound_message_id",
        "outbound_whatsapp_messages",
        "unsupported_whatsapp_messages",
        ["unsupported_inbound_message_id"],
        ["id"],
    )
    op.create_index(
        "ix_outbound_whatsapp_messages_unsupported_inbound_message_id",
        "outbound_whatsapp_messages",
        ["unsupported_inbound_message_id"],
        unique=True,
    )
    op.create_check_constraint(
        "ck_outbox_one_inbound_source",
        "outbound_whatsapp_messages",
        "(inbound_message_id IS NOT NULL AND "
        "unsupported_inbound_message_id IS NULL) OR "
        "(inbound_message_id IS NULL AND "
        "unsupported_inbound_message_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM outbound_whatsapp_messages
                WHERE unsupported_inbound_message_id IS NOT NULL
                   OR inbound_message_id IS NULL
                   OR complaint_id IS NULL
            ) OR EXISTS (
                SELECT 1 FROM unsupported_whatsapp_messages
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade while unlinked unsupported messages exist';
            END IF;
        END
        $$
        """
    )

    op.drop_constraint(
        "ck_outbox_one_inbound_source",
        "outbound_whatsapp_messages",
        type_="check",
    )
    op.drop_index(
        "ix_outbound_whatsapp_messages_unsupported_inbound_message_id",
        table_name="outbound_whatsapp_messages",
    )
    op.drop_constraint(
        "fk_outbound_whatsapp_messages_unsupported_inbound_message_id",
        "outbound_whatsapp_messages",
        type_="foreignkey",
    )
    op.drop_column(
        "outbound_whatsapp_messages",
        "unsupported_inbound_message_id",
    )
    op.alter_column(
        "outbound_whatsapp_messages",
        "inbound_message_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.alter_column(
        "outbound_whatsapp_messages",
        "complaint_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_index(
        op.f("ix_unsupported_whatsapp_messages_user_id"),
        table_name="unsupported_whatsapp_messages",
    )
    op.drop_table("unsupported_whatsapp_messages")
