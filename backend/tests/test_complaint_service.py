
from app.models.complaint import (
    ComplaintStatus,
    ConversationState,
)
from app.services.complaint_service import get_or_create_active_complaint
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
