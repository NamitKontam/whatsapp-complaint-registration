
import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy import (
    Enum as SQLAlchemyEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MessageType(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    LOCATION = "LOCATION"
    OTHER = "OTHER"


class ComplaintMessage(Base):
    __tablename__ = "complaint_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("complaints.id"),
        nullable=False,
        index=True,
    )

    whatsapp_message_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    message_type: Mapped[MessageType] = mapped_column(
        SQLAlchemyEnum(MessageType, name="message_type"),
        nullable=False,
    )

    text_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    media_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    media_mime_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    media_storage_key: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7),
        nullable=True,
    )

    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7),
        nullable=True,
    )


    caption: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    location_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    location_address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    interactive_reply_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    interactive_reply_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    interactive_reply_title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    raw_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )


    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    complaint = relationship("Complaint", back_populates="messages")
