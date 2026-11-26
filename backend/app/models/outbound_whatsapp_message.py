
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
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
    SENT = "SENT"
    FAILED = "FAILED"


class OutboundWhatsAppMessage(Base):
    __tablename__ = "outbound_whatsapp_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    inbound_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("complaint_messages.id"),
        unique=True,
        nullable=False,
        index=True,
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("complaints.id"),
        nullable=False,
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

    whatsapp_message_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
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
