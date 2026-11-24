
from datetime import UTC, datetime

from sqlalchemy import UUID, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.complaint import (
    Complaint,
    ComplaintStatus,
    ConversationState,
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

def submit_complaint(db: Session, complaint: Complaint) -> Complaint:
    if complaint.status != ComplaintStatus.DRAFT:
        raise ValueError("Only draft complaints can be submitted")

    if complaint.conversation_state == ConversationState.COMPLETED:
        raise ValueError("Complaint conversation is already completed")

    complaint.complaint_number = generate_complaint_number(db)
    complaint.status = ComplaintStatus.SUBMITTED
    complaint.conversation_state = ConversationState.COMPLETED
    complaint.completed_at = datetime.now(UTC)

    return complaint