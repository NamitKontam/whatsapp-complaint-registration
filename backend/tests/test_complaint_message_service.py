
import pytest

from app.models.complaint_message import (
    ComplaintMessage,
    MessageType,
)
from app.services.complaint_message_service import save_complaint_message
from app.services.complaint_service import get_or_create_active_complaint
from app.services.user_service import get_or_create_user


def create_test_complaint(db_session, phone_number="919876543210"):
    user = get_or_create_user(db_session, phone_number)
    return get_or_create_active_complaint(db_session, user.id)


def test_save_text_message(db_session):
    complaint = create_test_complaint(db_session)

    message = save_complaint_message(
        db=db_session,
        complaint_id=complaint.id,
        whatsapp_message_id="wamid.test001",
        message_type=MessageType.TEXT,
        text_content="There is no water supply in our area.",
    )

    assert message.id is not None
    assert message.complaint_id == complaint.id
    assert message.whatsapp_message_id == "wamid.test001"
    assert message.message_type == MessageType.TEXT
    assert message.text_content == "There is no water supply in our area."


def test_duplicate_whatsapp_message_is_not_saved_twice(db_session):
    complaint = create_test_complaint(db_session)

    first_message = save_complaint_message(
        db=db_session,
        complaint_id=complaint.id,
        whatsapp_message_id="wamid.duplicate001",
        message_type=MessageType.TEXT,
        text_content="Street light is not working.",
    )

    second_message = save_complaint_message(
        db=db_session,
        complaint_id=complaint.id,
        whatsapp_message_id="wamid.duplicate001",
        message_type=MessageType.TEXT,
        text_content="Street light is not working.",
    )

    assert first_message.id == second_message.id

    messages = (
        db_session.query(ComplaintMessage)
        .filter_by(complaint_id=complaint.id)
        .all()
    )

    assert len(messages) == 1


def test_multiple_messages_can_belong_to_same_complaint(db_session):
    complaint = create_test_complaint(db_session)

    first_message = save_complaint_message(
        db=db_session,
        complaint_id=complaint.id,
        whatsapp_message_id="wamid.multiple001",
        message_type=MessageType.TEXT,
        text_content="There is garbage on the road.",
    )

    second_message = save_complaint_message(
        db=db_session,
        complaint_id=complaint.id,
        whatsapp_message_id="wamid.multiple002",
        message_type=MessageType.TEXT,
        text_content="It has been there for three days.",
    )

    assert first_message.id != second_message.id

    messages = (
        db_session.query(ComplaintMessage)
        .filter_by(complaint_id=complaint.id)
        .all()
    )

    assert len(messages) == 2


def test_duplicate_message_id_cannot_be_assigned_to_another_complaint(db_session):
    first_complaint = create_test_complaint(
        db_session,
        "919876543210",
    )
    second_complaint = create_test_complaint(
        db_session,
        "919876543211",
    )

    save_complaint_message(
        db=db_session,
        complaint_id=first_complaint.id,
        whatsapp_message_id="wamid.crosscomplaint001",
        message_type=MessageType.TEXT,
        text_content="Water leakage near my house.",
    )

    with pytest.raises(ValueError):
        save_complaint_message(
            db=db_session,
            complaint_id=second_complaint.id,
            whatsapp_message_id="wamid.crosscomplaint001",
            message_type=MessageType.TEXT,
            text_content="A different complaint.",
        )
