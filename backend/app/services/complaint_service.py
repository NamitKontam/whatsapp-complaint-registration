
from app.models.complaint import (
    Complaint,
    ComplaintStatus,
    ConversationState,
)
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


def get_or_create_active_complaint(db: Session, user_id) -> Complaint:
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
