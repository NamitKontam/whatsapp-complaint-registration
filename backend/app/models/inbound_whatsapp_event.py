import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InboundEventType(str, Enum):
    MESSAGE = "MESSAGE"
    STATUS_CALLBACK = "STATUS_CALLBACK"


class InboundEventStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InboundWhatsAppEvent(Base):
    __tablename__ = "inbound_whatsapp_events"
    __table_args__ = (
        UniqueConstraint(
            "event_key",
            name="uq_inbound_whatsapp_events_event_key",
        ),
        Index(
            "uq_inbound_whatsapp_events_message_id",
            "whatsapp_message_id",
            unique=True,
            postgresql_where=text("event_type = 'MESSAGE'"),
        ),
        Index(
            "ix_inbound_whatsapp_events_status_available",
            "status",
            "available_at",
        ),
        Index(
            "ix_inbound_whatsapp_events_status_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    event_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    event_type: Mapped[InboundEventType] = mapped_column(
        SQLAlchemyEnum(InboundEventType, name="inbound_event_type"),
        nullable=False,
    )

    status: Mapped[InboundEventStatus] = mapped_column(
        SQLAlchemyEnum(InboundEventStatus, name="inbound_event_status"),
        nullable=False,
        default=InboundEventStatus.PENDING,
    )

    whatsapp_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    sender_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    provider_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    event_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    claimed_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
