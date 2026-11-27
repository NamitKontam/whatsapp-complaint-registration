
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ComplaintStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    ABANDONED = "ABANDONED"


class ConversationState(str, Enum):
    AWAITING_COMPLAINT = "AWAITING_COMPLAINT"
    ASKING_FOR_MORE_INFO = "ASKING_FOR_MORE_INFO"
    AWAITING_ADDITIONAL_INFO = "AWAITING_ADDITIONAL_INFO"
    COMPLETED = "COMPLETED"


class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    complaint_number: Mapped[str | None] = mapped_column(
        String(30),
        unique=True,
        nullable=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    status: Mapped[ComplaintStatus] = mapped_column(
        SQLAlchemyEnum(ComplaintStatus, name="complaint_status"),
        nullable=False,
        default=ComplaintStatus.DRAFT,
    )

    conversation_state: Mapped[ConversationState] = mapped_column(
        SQLAlchemyEnum(ConversationState, name="conversation_state"),
        nullable=False,
        default=ConversationState.AWAITING_COMPLAINT,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user = relationship("User", back_populates="complaints")

    messages = relationship("ComplaintMessage", back_populates="complaint")
