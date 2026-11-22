
from app.models.complaint import (
    Complaint,
    ComplaintStatus,
    ConversationState,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def get_or_create_active_complaint(
    db: Session,
    user_id,
) -> Complaint:
    """Retrieve the user's active draft complaint or create one."""

    # Look for an existing draft complaint.
    complaint = db.scalar(
        select(Complaint)
        .where(
            Complaint.user_id == user_id,
            Complaint.status == ComplaintStatus.DRAFT,
        )
        .order_by(Complaint.created_at.desc())
        .limit(1)
    )

    if complaint is not None:
        return complaint

    # No active draft exists, so create one.
    complaint = Complaint(
        user_id=user_id,
        status=ComplaintStatus.DRAFT,
        conversation_state=ConversationState.AWAITING_COMPLAINT,
    )

    db.add(complaint)

    # Send the INSERT to PostgreSQL without committing the transaction.
    db.flush()

    return complaint
