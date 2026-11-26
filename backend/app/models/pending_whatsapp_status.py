import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PendingWhatsAppStatus(Base):
    __tablename__ = "pending_whatsapp_statuses"
    __table_args__ = (
        UniqueConstraint(
            "whatsapp_message_id",
            "status",
            name="uq_pending_whatsapp_status_message_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    whatsapp_message_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    recipient_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    provider_timestamp: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
