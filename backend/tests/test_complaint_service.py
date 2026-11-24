

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.complaint import (
    Complaint,
    ComplaintStatus,
    ConversationState,
)
from app.services.complaint_service import (
    get_or_create_active_complaint,
    submit_complaint,
)
from app.services.user_service import get_or_create_user


def test_create_new_draft_complaint(db_session):
    user = get_or_create_user(db_session, "919876543210")

    complaint = get_or_create_active_complaint(db_session, user.id)

    assert complaint.id is not None
    assert complaint.user_id == user.id
    assert complaint.status == ComplaintStatus.DRAFT
    assert complaint.conversation_state == ConversationState.AWAITING_COMPLAINT
    assert complaint.complaint_number is None


def test_existing_active_draft_is_retrieved(db_session):
    user = get_or_create_user(db_session, "919876543210")

    first_complaint = get_or_create_active_complaint(db_session, user.id)
    second_complaint = get_or_create_active_complaint(db_session, user.id)

    assert first_complaint.id == second_complaint.id


def test_different_users_get_separate_complaints(db_session):
    first_user = get_or_create_user(db_session, "919876543210")
    second_user = get_or_create_user(db_session, "919876543211")

    first_complaint = get_or_create_active_complaint(db_session, first_user.id)
    second_complaint = get_or_create_active_complaint(db_session, second_user.id)

    assert first_complaint.id != second_complaint.id
    assert first_complaint.user_id == first_user.id
    assert second_complaint.user_id == second_user.id


def test_submitted_complaint_is_not_reused(db_session):
    user = get_or_create_user(db_session, "919876543210")

    first_complaint = get_or_create_active_complaint(db_session, user.id)

    first_complaint.status = ComplaintStatus.SUBMITTED
    first_complaint.conversation_state = ConversationState.COMPLETED
    db_session.flush()

    second_complaint = get_or_create_active_complaint(db_session, user.id)

    assert second_complaint.id != first_complaint.id
    assert second_complaint.status == ComplaintStatus.DRAFT
    assert second_complaint.conversation_state == ConversationState.AWAITING_COMPLAINT


def test_database_rejects_second_draft_for_same_user(db_session):
    user = get_or_create_user(db_session, "919876543210")

    # Create the first draft.
    first_complaint = get_or_create_active_complaint(db_session, user.id)

    # Directly attempt to insert another draft, bypassing our service.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        duplicate_complaint = Complaint(
            user_id=user.id,
            status=ComplaintStatus.DRAFT,
            conversation_state=ConversationState.AWAITING_COMPLAINT,
        )

        db_session.add(duplicate_complaint)
        db_session.flush()

    # Verify that only the original draft exists.
    drafts = db_session.scalars(
        select(Complaint).where(
            Complaint.user_id == user.id,
            Complaint.status == ComplaintStatus.DRAFT,
        )
    ).all()

    assert len(drafts) == 1
    assert drafts[0].id == first_complaint.id

def test_submit_draft_complaint(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    submitted_complaint = submit_complaint(db_session, complaint)

    assert submitted_complaint.complaint_number.startswith("CMP-")
    assert submitted_complaint.status == ComplaintStatus.SUBMITTED
    assert submitted_complaint.conversation_state == ConversationState.COMPLETED
    assert submitted_complaint.completed_at is not None
    assert submitted_complaint.completed_at.tzinfo is not None


def test_cannot_submit_already_submitted_complaint(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    submit_complaint(db_session, complaint)
    original_reference = complaint.complaint_number

    with pytest.raises(ValueError, match="Only draft complaints can be submitted"):
        submit_complaint(db_session, complaint)

    assert complaint.complaint_number == original_reference