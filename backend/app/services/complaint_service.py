
from datetime import UTC, datetime

from sqlalchemy import UUID, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.complaint import (
    Complaint,
    ComplaintStatus,
    ConversationState,
)
from app.models.complaint_message import (
    ComplaintMessage,
    MessageType,
)
from app.services.reference_service import generate_complaint_number


def get_or_create_active_complaint(db: Session, user_id: UUID) -> Complaint:
    statement = (
        insert(Complaint)
        .values(
            user_id=user_id,
            status=ComplaintStatus.DRAFT,
            conversation_state=ConversationState.AWAITING_COMPLAINT,
        )
        .on_conflict_do_nothing(
            index_elements=[Complaint.user_id],
            index_where=text("status = 'DRAFT'"),
        )
        .returning(Complaint)
    )

    complaint = db.scalars(statement).one_or_none()

    if complaint is not None:
        return complaint

    complaint = db.scalar(
        select(Complaint).where(
            Complaint.user_id == user_id,
            Complaint.status == ComplaintStatus.DRAFT,
        )
    )

    if complaint is None:
        raise RuntimeError("Unable to retrieve or create active complaint")

    return complaint



def has_meaningful_complaint_message(
    db: Session,
    complaint_id: UUID,
) -> bool:
    """Check whether a complaint contains meaningful information."""

    normalized_text = func.lower(
        func.regexp_replace(
            ComplaintMessage.text_content,
            r"\s",
            "",
            "g",
        )
    )

    statement = select(ComplaintMessage.id).where(
        ComplaintMessage.complaint_id == complaint_id,
        or_(
            # Non-empty text message that is not a conversation command
            (
                (ComplaintMessage.message_type == MessageType.TEXT)
                & (
                    func.regexp_replace(
                        ComplaintMessage.text_content,
                        r"\s",
                        "",
                        "g",
                    )
                    != ""
                )
                & normalized_text.notin_(("yes", "y", "no", "n"))
                & ComplaintMessage.text_content.is_not(None)
            ),
            # Image or audio with a media ID
            (
                ComplaintMessage.message_type.in_(
                    [MessageType.IMAGE, MessageType.AUDIO]
                )
                & ComplaintMessage.media_id.is_not(None)
            ),
            # Location with both coordinates
            (
                (ComplaintMessage.message_type == MessageType.LOCATION)
                & ComplaintMessage.latitude.is_not(None)
                & ComplaintMessage.longitude.is_not(None)
            ),
        ),
    ).limit(1)

    return db.scalar(statement) is not None


def submit_complaint(db: Session, complaint: Complaint) -> Complaint:
    if complaint.status != ComplaintStatus.DRAFT:
        raise ValueError("Only draft complaints can be submitted")

    if complaint.conversation_state == ConversationState.COMPLETED:
        raise ValueError("Complaint conversation is already completed")

    if not has_meaningful_complaint_message(db, complaint.id):
        raise ValueError(
            "Cannot submit a complaint without meaningful information"
        )

    complaint.complaint_number = generate_complaint_number(db)
    complaint.status = ComplaintStatus.SUBMITTED
    complaint.conversation_state = ConversationState.COMPLETED
    complaint.completed_at = datetime.now(UTC)

    return complaint