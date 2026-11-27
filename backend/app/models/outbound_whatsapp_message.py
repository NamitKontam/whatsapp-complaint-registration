import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OutboundMessageStatus(str, Enum):
    PENDING = "PENDING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class OutboundWhatsAppMessage(Base):
    __tablename__ = "outbound_whatsapp_messages"
    __table_args__ = (
        CheckConstraint(
            "(inbound_message_id IS NOT NULL AND "
            "unsupported_inbound_message_id IS NULL) OR "
            "(inbound_message_id IS NULL AND "
            "unsupported_inbound_message_id IS NOT NULL)",
            name="ck_outbox_one_inbound_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("complaint_messages.id"),
        unique=True,
        nullable=True,
        index=True,
    )

    unsupported_inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("unsupported_whatsapp_messages.id"),
        unique=True,
        nullable=True,
        index=True,
    )

    complaint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("complaints.id"),
        nullable=True,
        index=True,
    )

    recipient: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    interactive_buttons: Mapped[list[dict[str, str]] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    status: Mapped[OutboundMessageStatus] = mapped_column(
        SQLAlchemyEnum(
            OutboundMessageStatus,
            name="outbound_message_status",
        ),
        nullable=False,
        default=OutboundMessageStatus.PENDING,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    whatsapp_message_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )

    provider_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    provider_status_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    inbound_message = relationship("ComplaintMessage")
    complaint = relationship("Complaint")
    unsupported_inbound_message = relationship("UnsupportedWhatsAppMessage")
