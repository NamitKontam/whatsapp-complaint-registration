
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.complaint import Complaint, ComplaintStatus, ConversationState
from app.models.complaint_message import ComplaintMessage, MessageType
from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.models.user import User
from app.schemas.whatsapp_message import (
    IncomingWhatsAppMessage,
    InteractiveReply,
    InteractiveReplyType,
    MessageKind,
)
from app.services.complaint_processing_service import (
    _build_duplicate_result,
    _classify_message_event,
    _get_locked_user,
    _map_message_type,
    _save_incoming_message,
    _try_submit_complaint,
    _update_conversation_state,
    process_incoming_message,
)
from app.services.complaint_service import get_or_create_active_complaint
from app.services.conversation_service import ConversationEvent
from app.services.user_service import get_or_create_user
from app.services.whatsapp_reply_service import get_whatsapp_reply


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
        whatsapp_number=kwargs.pop(
            "whatsapp_number",
            "919876543210",
        ),
        whatsapp_message_id=kwargs.pop(
            "whatsapp_message_id",
            "wamid.test123",
        ),
        timestamp=kwargs.pop(
            "timestamp",
            datetime.now(UTC),
        ),
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


def test_get_locked_user_creates_and_returns_user(db_session):
    user = _get_locked_user(
        db=db_session,
        whatsapp_number="919876543210",
    )

    assert user.id is not None
    assert user.whatsapp_number == "+919876543210"

    db_session.commit()

    persisted_user = db_session.get(User, user.id)
    assert persisted_user is not None


def test_process_incoming_message_creates_user_and_draft(
    db_session,
):
    message = create_incoming_message(
        message_type=MessageKind.TEXT,
        text_content="There is a water leak on my street",
    )

    result = process_incoming_message(
        db=db_session,
        message=message,
    )

    assert result.is_duplicate is False
    assert result.submitted is False
    assert result.needs_more_information is False
    assert result.event == ConversationEvent.MESSAGE_RECEIVED

    assert result.user.whatsapp_number == "+919876543210"

    assert result.complaint.status == ComplaintStatus.DRAFT
    assert (
        result.complaint.conversation_state
        == ConversationState.ASKING_FOR_MORE_INFO
    )

    assert result.message.text_content == (
        "There is a water leak on my street"
    )

    assert result.message.complaint_id == result.complaint.id


def test_process_incoming_message_yes_asks_for_additional_info(
    db_session,
):
    first_message = create_incoming_message(
        message_type=MessageKind.TEXT,
        text_content="There is a water leak on my street",
        whatsapp_message_id="wamid.first-complaint",
    )

    first_result = process_incoming_message(
        db=db_session,
        message=first_message,
    )

    yes_message = create_incoming_message(
        message_type=MessageKind.TEXT,
        text_content="Yes",
        whatsapp_message_id="wamid.yes-reply",
    )

    yes_result = process_incoming_message(
        db=db_session,
        message=yes_message,
    )

    assert yes_result.is_duplicate is False
    assert yes_result.event == ConversationEvent.YES
    assert yes_result.submitted is False

    assert yes_result.user.id == first_result.user.id
    assert yes_result.complaint.id == first_result.complaint.id

    assert (
        yes_result.complaint.conversation_state
        == ConversationState.AWAITING_ADDITIONAL_INFO
    )

    assert yes_result.message.text_content == "Yes"


def test_process_incoming_message_saves_additional_information(
    db_session,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.first",
        ),
    )

    yes_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="Yes",
            whatsapp_message_id="wamid.yes",
        ),
    )

    details_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="The leak is near the main road",
            whatsapp_message_id="wamid.details",
        ),
    )

    assert details_result.is_duplicate is False
    assert details_result.submitted is False
    assert details_result.needs_more_information is False
    assert details_result.event == ConversationEvent.MESSAGE_RECEIVED

    assert details_result.user.id == first_result.user.id
    assert details_result.complaint.id == first_result.complaint.id
    assert details_result.complaint.id == yes_result.complaint.id

    assert (
        details_result.complaint.conversation_state
        == ConversationState.ASKING_FOR_MORE_INFO
    )

    assert details_result.message.text_content == (
        "The leak is near the main road"
    )


def test_process_incoming_message_no_submits_complaint(
    db_session,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.submission-details",
        ),
    )

    no_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="No",
            whatsapp_message_id="wamid.submission-no",
        ),
    )

    assert no_result.is_duplicate is False
    assert no_result.event == ConversationEvent.NO
    assert no_result.submitted is True
    assert no_result.needs_more_information is False

    assert no_result.user.id == first_result.user.id
    assert no_result.complaint.id == first_result.complaint.id

    assert no_result.complaint.status == ComplaintStatus.SUBMITTED
    assert (
        no_result.complaint.conversation_state
        == ConversationState.COMPLETED
    )

    assert no_result.complaint.complaint_number is not None

def test_process_incoming_message_no_without_meaningful_information(
    db_session,
):
    result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="No",
            whatsapp_message_id="wamid.empty-submission-no",
        ),
    )

    assert result.is_duplicate is False
    assert result.event == ConversationEvent.NO
    assert result.submitted is False
    assert result.needs_more_information is True

    assert result.complaint.status == ComplaintStatus.DRAFT
    assert (
        result.complaint.conversation_state
        == ConversationState.ASKING_FOR_MORE_INFO
    )
    assert result.complaint.complaint_number is None


def test_process_incoming_message_duplicate_webhook(
    db_session,
):
    message = create_incoming_message(
        message_type=MessageKind.TEXT,
        text_content="There is a water leak on my street",
        whatsapp_message_id="wamid.duplicate-webhook",
    )

    # First delivery: process and save the message.
    first_result = process_incoming_message(
        db=db_session,
        message=message,
    )

    # Second delivery: Meta retries the same webhook.
    duplicate_result = process_incoming_message(
        db=db_session,
        message=message,
    )

    assert first_result.is_duplicate is False

    assert duplicate_result.is_duplicate is True
    assert duplicate_result.user.id == first_result.user.id
    assert duplicate_result.complaint.id == first_result.complaint.id
    assert duplicate_result.message.id == first_result.message.id

    # The message must exist only once in the database.
    message_count = (
        db_session.query(ComplaintMessage)
        .filter(
            ComplaintMessage.whatsapp_message_id
            == "wamid.duplicate-webhook"
        )
        .count()
    )

    assert message_count == 1


def test_duplicate_no_webhook_does_not_create_new_draft(
    db_session,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.duplicate-no-details",
        ),
    )

    no_message = create_incoming_message(
        message_type=MessageKind.TEXT,
        text_content="No",
        whatsapp_message_id="wamid.duplicate-no-submit",
    )

    # First delivery of No: submit the complaint.
    no_result = process_incoming_message(
        db=db_session,
        message=no_message,
    )

    # Meta retries the exact same No message.
    duplicate_result = process_incoming_message(
        db=db_session,
        message=no_message,
    )

    assert no_result.submitted is True
    assert no_result.complaint.status == ComplaintStatus.SUBMITTED

    assert duplicate_result.is_duplicate is True
    assert duplicate_result.submitted is True
    assert duplicate_result.user.id == first_result.user.id
    assert duplicate_result.complaint.id == no_result.complaint.id
    assert duplicate_result.message.id == no_result.message.id

    # The retry must not create another complaint for this user.
    complaint_count = (
        db_session.query(Complaint)
        .filter(Complaint.user_id == first_result.user.id)
        .count()
    )

    assert complaint_count == 1


def test_process_incoming_message_creates_pending_outbox_reply(
    db_session,
):
    message = create_incoming_message(
        message_type=MessageKind.TEXT,
        text_content="There is a water leak on my street",
        whatsapp_message_id="wamid.outbox-new-message",
    )

    result = process_incoming_message(
        db=db_session,
        message=message,
    )

    outbound_message = (
        db_session.query(OutboundWhatsAppMessage)
        .filter(
            OutboundWhatsAppMessage.inbound_message_id
            == result.message.id
        )
        .one()
    )

    expected_reply = get_whatsapp_reply(result)

    assert expected_reply is not None
    assert outbound_message.inbound_message_id == result.message.id
    assert outbound_message.complaint_id == result.complaint.id
    assert outbound_message.recipient == message.whatsapp_number
    assert outbound_message.body == expected_reply
    assert outbound_message.status == OutboundMessageStatus.PENDING
    assert outbound_message.attempts == 0


def test_duplicate_incoming_message_does_not_create_duplicate_outbox(
    db_session,
):
    message = create_incoming_message(
        message_type=MessageKind.TEXT,
        text_content="There is a water leak on my street",
        whatsapp_message_id="wamid.outbox-duplicate",
    )

    first_result = process_incoming_message(
        db=db_session,
        message=message,
    )

    duplicate_result = process_incoming_message(
        db=db_session,
        message=message,
    )

    outbound_messages = (
        db_session.query(OutboundWhatsAppMessage)
        .filter(
            OutboundWhatsAppMessage.inbound_message_id
            == first_result.message.id
        )
        .all()
    )

    assert first_result.is_duplicate is False
    assert duplicate_result.is_duplicate is True
    assert duplicate_result.message.id == first_result.message.id

    # Only the original inbound message should have an outbox record.
    assert len(outbound_messages) == 1

    outbound_message = outbound_messages[0]

    assert outbound_message.status == OutboundMessageStatus.PENDING
    assert outbound_message.body == get_whatsapp_reply(first_result)
