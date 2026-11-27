"""add complaint draft expiry

Revision ID: b6c4d93e2a11
Revises: a93e50d74f2c
Create Date: 2026-11-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6c4d93e2a11"
down_revision: str | Sequence[str] | None = "a93e50d74f2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE complaint_status ADD VALUE 'ABANDONED'")
    op.add_column(
        "complaints",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE complaints AS complaint
        SET last_activity_at = COALESCE(
            (
                SELECT MAX(message.created_at)
                FROM complaint_messages AS message
                WHERE message.complaint_id = complaint.id
                    AND (
                        (
                            message.message_type = 'TEXT'
                            AND regexp_replace(
                                COALESCE(message.text_content, ''),
                                '\\s',
                                '',
                                'g'
                            ) <> ''
                            AND lower(regexp_replace(
                                COALESCE(message.text_content, ''),
                                '\\s',
                                '',
                                'g'
                            )) NOT IN ('yes', 'y', 'no', 'n')
                        )
                        OR (
                            message.message_type IN ('IMAGE', 'AUDIO')
                            AND message.media_id IS NOT NULL
                        )
                        OR (
                            message.message_type = 'LOCATION'
                            AND message.latitude IS NOT NULL
                            AND message.longitude IS NOT NULL
                        )
                    )
            ),
            complaint.created_at
        )
        WHERE complaint.status = 'DRAFT'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM complaints WHERE status = 'ABANDONED'
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade while abandoned complaints exist';
            END IF;
        END
        $$
        """
    )

    op.drop_column("complaints", "last_activity_at")
    op.drop_index(
        "uq_complaints_one_draft_per_user",
        table_name="complaints",
    )
    op.execute("ALTER TYPE complaint_status RENAME TO complaint_status_with_abandoned")
    op.execute(
        """
        CREATE TYPE complaint_status AS ENUM (
            'DRAFT',
            'SUBMITTED',
            'UNDER_REVIEW',
            'RESOLVED',
            'REJECTED'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE complaints
        ALTER COLUMN status TYPE complaint_status
        USING status::text::complaint_status
        """
    )
    op.execute("DROP TYPE complaint_status_with_abandoned")
    op.create_index(
        "uq_complaints_one_draft_per_user",
        "complaints",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )
