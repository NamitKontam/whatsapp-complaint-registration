
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.complaint import ComplaintStatus, ConversationState
from app.models.complaint_message import ComplaintMessage, MessageType
from app.schemas.whatsapp_message import (
    IncomingWhatsAppMessage,
    InteractiveReply,
    InteractiveReplyType,
    MessageKind,
)
from app.services.complaint_processing_service import (
    _build_duplicate_result,
    _classify_message_event,
    _map_message_type,
    _save_incoming_message,
    _try_submit_complaint,
    _update_conversation_state,
)
from app.services.complaint_service import get_or_create_active_complaint
from app.services.conversation_service import ConversationEvent
from app.services.user_service import get_or_create_user


@pytest.mark.parametrize(
    ("incoming_type", "expected_type"),
    [
        (MessageKind.TEXT, MessageType.TEXT),
        (MessageKind.IMAGE, MessageType.IMAGE),
        (MessageKind.AUDIO, MessageType.AUDIO),
        (MessageKind.LOCATION, MessageType.LOCATION),
        (MessageKind.INTERACTIVE, MessageType.OTHER),
        (MessageKind.UNSUPPORTED, MessageType.OTHER),
    ],
)
def test_map_message_type(incoming_type, expected_type):
    assert _map_message_type(incoming_type) == expected_type



def create_incoming_message(
    message_type: MessageKind,
    **kwargs,
) -> IncomingWhatsAppMessage:
    return IncomingWhatsAppMessage(
        whatsapp_number="919876543210",
        whatsapp_message_id="wamid.test123",
        timestamp=datetime.now(UTC),
        message_type=message_type,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("text", "expected_event"),
    [
        ("Yes", ConversationEvent.YES),
        ("yes", ConversationEvent.YES),
        ("Y", ConversationEvent.YES),
        ("No", ConversationEvent.NO),
        ("no", ConversationEvent.NO),
        ("N", ConversationEvent.NO),
        ("My street has a water leak", ConversationEvent.MESSAGE_RECEIVED),
    ],
)
def test_classify_text_message(text, expected_event):
    message = create_incoming_message(
        MessageKind.TEXT,
        text_content=text,
    )

    assert _classify_message_event(message) == expected_event


@pytest.mark.parametrize(
    ("reply_id", "expected_event"),
    [
        ("complaint_yes", ConversationEvent.YES),
        ("complaint_no", ConversationEvent.NO),
        ("unknown_id", ConversationEvent.MESSAGE_RECEIVED),
    ],
)
def test_classify_interactive_message(reply_id, expected_event):
    message = create_incoming_message(
        MessageKind.INTERACTIVE,
        interactive_reply=InteractiveReply(
            reply_type=InteractiveReplyType.BUTTON,
            reply_id=reply_id,
            title="Some button",
        ),
    )

    assert _classify_message_event(message) == expected_event


def test_image_caption_no_is_not_a_submission_command():
    message = create_incoming_message(
        MessageKind.IMAGE,
        media_id="media123",
        caption="No",
    )

    assert (
        _classify_message_event(message)
        == ConversationEvent.MESSAGE_RECEIVED
    )


def test_save_incoming_image_message_with_metadata(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    incoming_message = create_incoming_message(
        MessageKind.IMAGE,
        media_id="media123",
        media_mime_type="image/jpeg",
        caption="Large pothole near the main road",
        raw_type="image",
    )

    saved_message, is_new = _save_incoming_message(
        db_session,
        complaint,
        incoming_message,
    )

    assert is_new is True
    assert saved_message.complaint_id == complaint.id
    assert saved_message.whatsapp_message_id == "wamid.test123"
    assert saved_message.message_type == MessageType.IMAGE
    assert saved_message.media_id == "media123"
    assert saved_message.media_mime_type == "image/jpeg"
    assert saved_message.caption == "Large pothole near the main road"
    assert saved_message.raw_type == "image"

    stored_message = db_session.get(
        ComplaintMessage,
        saved_message.id,
    )

    assert stored_message is not None
    assert stored_message.media_id == "media123"


def test_save_incoming_location_message(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    incoming_message = create_incoming_message(
        MessageKind.LOCATION,
        latitude=Decimal("17.6599"),
        longitude=Decimal("75.9064"),
        location_name="Solapur",
        location_address="Solapur, Maharashtra",
    )

    saved_message, is_new = _save_incoming_message(
        db_session,
        complaint,
        incoming_message,
    )

    assert is_new is True
    assert saved_message.message_type == MessageType.LOCATION
    assert saved_message.latitude == Decimal("17.6599")
    assert saved_message.longitude == Decimal("75.9064")
    assert saved_message.location_name == "Solapur"
    assert saved_message.location_address == "Solapur, Maharashtra"


def test_save_incoming_interactive_message(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    incoming_message = create_incoming_message(
        MessageKind.INTERACTIVE,
        interactive_reply=InteractiveReply(
            reply_type=InteractiveReplyType.BUTTON,
            reply_id="complaint_yes",
            title="Yes",
        ),
    )

    saved_message, is_new = _save_incoming_message(
        db_session,
        complaint,
        incoming_message,
    )

    assert is_new is True
    assert saved_message.message_type == MessageType.OTHER
    assert saved_message.interactive_reply_type == "BUTTON"
    assert saved_message.interactive_reply_id == "complaint_yes"
    assert saved_message.interactive_reply_title == "Yes"


def test_build_duplicate_result_returns_existing_complaint(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    saved_message, _ = _save_incoming_message(
        db_session,
        complaint,
        create_incoming_message(
            MessageKind.TEXT,
            text_content="Water supply is unavailable.",
        ),
    )

    result = _build_duplicate_result(
        db_session,
        saved_message,
        user,
    )

    assert result.user.id == user.id
    assert result.complaint.id == complaint.id
    assert result.message.id == saved_message.id
    assert result.event is None
    assert result.is_duplicate is True
    assert result.submitted is False
    assert result.needs_more_information is False


def test_build_duplicate_result_rejects_another_users_message(
    db_session,
):
    first_user = get_or_create_user(db_session, "919876543210")
    second_user = get_or_create_user(db_session, "919876543211")

    complaint = get_or_create_active_complaint(
        db_session,
        first_user.id,
    )

    saved_message, _ = _save_incoming_message(
        db_session,
        complaint,
        create_incoming_message(
            MessageKind.TEXT,
            text_content="Water supply is unavailable.",
        ),
    )

    with pytest.raises(
        ValueError,
        match="another user",
    ):
        _build_duplicate_result(
            db_session,
            saved_message,
            second_user,
        )


@pytest.mark.parametrize(
    ("initial_state", "event", "expected_state"),
    [
        (
            ConversationState.AWAITING_COMPLAINT,
            ConversationEvent.MESSAGE_RECEIVED,
            ConversationState.ASKING_FOR_MORE_INFO,
        ),
        (
            ConversationState.ASKING_FOR_MORE_INFO,
            ConversationEvent.YES,
            ConversationState.AWAITING_ADDITIONAL_INFO,
        ),
        (
            ConversationState.ASKING_FOR_MORE_INFO,
            ConversationEvent.NO,
            ConversationState.COMPLETED,
        ),
        (
            ConversationState.AWAITING_ADDITIONAL_INFO,
            ConversationEvent.MESSAGE_RECEIVED,
            ConversationState.ASKING_FOR_MORE_INFO,
        ),
    ],
)
def test_update_conversation_state(
    db_session,
    initial_state,
    event,
    expected_state,
):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    complaint.conversation_state = initial_state

    _update_conversation_state(complaint, event)

    assert complaint.conversation_state == expected_state


def test_try_submit_complaint_with_meaningful_message(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    _save_incoming_message(
        db_session,
        complaint,
        create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leakage near my house.",
        ),
    )

    submitted = _try_submit_complaint(db_session, complaint)

    assert submitted is True
    assert complaint.status == ComplaintStatus.SUBMITTED
    assert complaint.conversation_state == ConversationState.COMPLETED
    assert complaint.complaint_number.startswith("CMP-")


def test_try_submit_complaint_without_meaningful_message(db_session):
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)

    submitted = _try_submit_complaint(db_session, complaint)

    assert submitted is False
    assert complaint.status == ComplaintStatus.DRAFT
    assert (
        complaint.conversation_state
        == ConversationState.ASKING_FOR_MORE_INFO
    )
    assert complaint.complaint_number is None
    assert complaint.completed_at is None
