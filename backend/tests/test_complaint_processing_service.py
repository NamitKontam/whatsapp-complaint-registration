import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.complaint import Complaint, ComplaintStatus, ConversationState
from app.models.complaint_message import ComplaintMessage, MessageType
from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.models.unsupported_whatsapp_message import UnsupportedWhatsAppMessage
from app.models.user import User
from app.schemas.whatsapp_message import (
    IncomingWhatsAppMessage,
    InteractiveReply,
    InteractiveReplyType,
    MessageKind,
)
from app.services import complaint_processing_service as processing_service
from app.services.complaint_processing_service import (
    _build_duplicate_result,
    _classify_message_event,
    _get_locked_user,
    _map_message_type,
    _save_incoming_message,
    _try_submit_complaint,
    _update_conversation_state,
)
from app.services.complaint_processing_service import (
    process_incoming_message as async_process_incoming_message,
)
from app.services.complaint_service import (
    get_or_create_active_complaint,
    has_meaningful_complaint_message,
)
from app.services.conversation_service import ConversationEvent
from app.services.user_service import get_or_create_user
from app.services.whatsapp_media_service import (
    WhatsAppMediaDownload,
    WhatsAppMediaDownloadError,
)
from app.services.whatsapp_reply_service import (
    InteractiveWhatsAppReply,
    get_whatsapp_reply,
)


def process_incoming_message(**kwargs):
    return asyncio.run(async_process_incoming_message(**kwargs))


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


def create_interactive_button_message(
    reply_id: str,
    whatsapp_message_id: str,
) -> IncomingWhatsAppMessage:
    return create_incoming_message(
        MessageKind.INTERACTIVE,
        whatsapp_message_id=whatsapp_message_id,
        interactive_reply=InteractiveReply(
            reply_type=InteractiveReplyType.BUTTON,
            reply_id=reply_id,
            title=reply_id,
        ),
    )


def create_unsupported_message(
    whatsapp_message_id: str,
    whatsapp_number: str = "919876543210",
) -> IncomingWhatsAppMessage:
    return create_incoming_message(
        MessageKind.UNSUPPORTED,
        whatsapp_number=whatsapp_number,
        whatsapp_message_id=whatsapp_message_id,
        raw_type="sticker",
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
        ("hi", ConversationEvent.MESSAGE_RECEIVED),
        ("hello", ConversationEvent.MESSAGE_RECEIVED),
        ("hey", ConversationEvent.MESSAGE_RECEIVED),
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
        ("complaint_add_more", ConversationEvent.YES),
        ("complaint_submit", ConversationEvent.NO),
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


@pytest.mark.parametrize(
    "conversation_state",
    [
        ConversationState.AWAITING_COMPLAINT,
        ConversationState.ASKING_FOR_MORE_INFO,
        ConversationState.AWAITING_ADDITIONAL_INFO,
    ],
)
def test_unsupported_message_preserves_active_draft_state(
    db_session,
    conversation_state,
):
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    user = get_or_create_user(db_session, "919876543210")
    complaint = get_or_create_active_complaint(db_session, user.id)
    complaint.created_at = base_time
    complaint.last_activity_at = base_time
    complaint.conversation_state = conversation_state
    db_session.flush()
    existing_message = None
    if conversation_state != ConversationState.AWAITING_COMPLAINT:
        existing_message = ComplaintMessage(
            complaint_id=complaint.id,
            whatsapp_message_id=f"wamid.unsupported-history-{conversation_state.value}",
            message_type=MessageType.TEXT,
            text_content="Previously reported issue.",
        )
        db_session.add(existing_message)
        db_session.flush()
    had_meaningful_details = has_meaningful_complaint_message(
        db_session,
        complaint.id,
    )

    result = process_incoming_message(
        db=db_session,
        message=create_unsupported_message("wamid.unsupported-active"),
        now=base_time + timedelta(hours=1),
    )

    assert result.complaint.id == complaint.id
    assert result.is_unsupported is True
    assert result.event is None
    assert result.submitted is False
    assert result.complaint.status == ComplaintStatus.DRAFT
    assert result.complaint.conversation_state == conversation_state
    assert result.complaint.last_activity_at == base_time
    assert result.message.message_type == MessageType.OTHER
    assert result.message.text_content is None
    assert result.message.media_id is None
    assert (
        has_meaningful_complaint_message(db_session, complaint.id)
        is had_meaningful_details
    )
    if existing_message is not None:
        assert db_session.get(ComplaintMessage, existing_message.id).text_content == (
            "Previously reported issue."
        )

    reply = get_whatsapp_reply(result)
    assert isinstance(reply, str)
    assert "text" in reply
    assert "photo/image" in reply
    assert "audio/voice" in reply
    assert "location" in reply
    assert db_session.query(Complaint).filter(Complaint.user_id == user.id).count() == 1
    assert (
        db_session.query(OutboundWhatsAppMessage)
        .filter_by(inbound_message_id=result.message.id)
        .count()
        == 1
    )


def test_unsupported_message_without_draft_uses_unlinked_outbox(db_session):
    result = process_incoming_message(
        db=db_session,
        message=create_unsupported_message("wamid.unsupported-no-draft"),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    unsupported_message = db_session.query(UnsupportedWhatsAppMessage).one()
    outbound_message = db_session.query(OutboundWhatsAppMessage).one()

    assert result.complaint is None
    assert result.message is None
    assert result.is_unsupported is True
    assert unsupported_message.user_id == result.user.id
    assert unsupported_message.raw_type == "sticker"
    assert outbound_message.inbound_message_id is None
    assert outbound_message.complaint_id is None
    assert outbound_message.unsupported_inbound_message_id == unsupported_message.id
    assert db_session.query(Complaint).count() == 0

    duplicate_result = process_incoming_message(
        db=db_session,
        message=create_unsupported_message("wamid.unsupported-no-draft"),
        now=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=1),
    )

    assert duplicate_result.is_duplicate is True
    assert duplicate_result.unsupported_inbound_message_id == unsupported_message.id
    assert get_whatsapp_reply(duplicate_result) is None
    assert db_session.query(UnsupportedWhatsAppMessage).count() == 1
    assert db_session.query(OutboundWhatsAppMessage).count() == 1
    assert db_session.query(Complaint).count() == 0


def test_unsupported_message_does_not_create_replacement_for_expired_draft(
    db_session,
):
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Water is leaking on my street.",
            whatsapp_message_id="wamid.unsupported-expiry-details",
        ),
        now=base_time,
    )
    first_result.complaint.created_at = base_time
    first_result.complaint.last_activity_at = base_time
    db_session.flush()

    result = process_incoming_message(
        db=db_session,
        message=create_unsupported_message("wamid.unsupported-expiry"),
        now=base_time + timedelta(hours=24),
    )

    assert result.complaint is None
    assert first_result.complaint.status == ComplaintStatus.ABANDONED
    assert db_session.query(Complaint).count() == 1
    assert db_session.query(UnsupportedWhatsAppMessage).count() == 1


def test_unsupported_duplicate_id_cannot_cross_users(db_session):
    process_incoming_message(
        db=db_session,
        message=create_unsupported_message(
            "wamid.unsupported-cross-user",
            "919876543210",
        ),
    )

    with pytest.raises(ValueError, match="another user"):
        process_incoming_message(
            db=db_session,
            message=create_unsupported_message(
                "wamid.unsupported-cross-user",
                "919876543211",
            ),
        )

    assert db_session.query(UnsupportedWhatsAppMessage).count() == 1
    assert db_session.query(Complaint).count() == 0


def test_image_caption_no_is_not_a_submission_command():
    message = create_incoming_message(
        MessageKind.IMAGE,
        media_id="media123",
        caption="No",
    )

    assert _classify_message_event(message) == ConversationEvent.MESSAGE_RECEIVED


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


@pytest.mark.parametrize(
    ("message_type", "media_mime_type", "raw_type", "caption"),
    [
        (MessageKind.IMAGE, "image/jpeg", "image", "Pothole on Main Road"),
        (MessageKind.AUDIO, "audio/ogg; codecs=opus", "voice", None),
    ],
)
def test_process_media_message_persists_storage_key_and_metadata(
    db_session,
    monkeypatch,
    message_type,
    media_mime_type,
    raw_type,
    caption,
):
    downloads = []

    async def download_media(media_id, *, expected_mime_type):
        downloads.append((media_id, expected_mime_type))
        extension = ".jpg" if message_type == MessageKind.IMAGE else ".ogg"
        return WhatsAppMediaDownload(
            storage_key=f"{'a' * 32}{extension}",
            media_id=media_id,
            mime_type=media_mime_type.split(";")[0],
            size_bytes=128,
            sha256="a" * 64,
        )

    monkeypatch.setattr(
        processing_service,
        "download_whatsapp_media",
        download_media,
    )

    message = create_incoming_message(
        message_type,
        whatsapp_message_id=f"wamid.media-{raw_type}",
        media_id=f"media-{raw_type}",
        media_mime_type=media_mime_type,
        caption=caption,
        raw_type=raw_type,
    )
    activity_time = datetime(2026, 1, 1, tzinfo=UTC)
    result = process_incoming_message(
        db=db_session,
        message=message,
        now=activity_time,
    )
    stored_message = db_session.get(ComplaintMessage, result.message.id)

    assert downloads == [(message.media_id, media_mime_type)]
    assert result.message.whatsapp_message_id == message.whatsapp_message_id
    assert stored_message is not None
    expected_extension = ".jpg" if message_type == MessageKind.IMAGE else ".ogg"
    assert stored_message.media_storage_key == f"{'a' * 32}{expected_extension}"
    assert stored_message.media_id == message.media_id
    assert stored_message.media_mime_type == media_mime_type
    assert stored_message.caption == caption
    assert stored_message.raw_type == raw_type
    assert stored_message.message_type == (
        MessageType.IMAGE if message_type == MessageKind.IMAGE else MessageType.AUDIO
    )
    assert result.complaint.last_activity_at == activity_time


def test_location_message_updates_draft_activity(db_session):
    activity_time = datetime(2026, 1, 1, tzinfo=UTC)

    result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.LOCATION,
            whatsapp_message_id="wamid.location-activity",
            latitude=Decimal("17.6599"),
            longitude=Decimal("75.9064"),
            location_name="Central Gate",
            location_address="Main Road",
        ),
        now=activity_time,
    )

    assert result.complaint.last_activity_at == activity_time


def test_duplicate_media_webhook_does_not_download_twice(
    db_session,
    monkeypatch,
):
    downloads = []

    async def download_media(media_id, *, expected_mime_type):
        downloads.append(media_id)
        return WhatsAppMediaDownload(
            storage_key=f"{'b' * 32}.png",
            media_id=media_id,
            mime_type="image/png",
            size_bytes=32,
            sha256="b" * 64,
        )

    monkeypatch.setattr(
        processing_service,
        "download_whatsapp_media",
        download_media,
    )
    message = create_incoming_message(
        MessageKind.IMAGE,
        whatsapp_message_id="wamid.duplicate-image",
        media_id="duplicate-media",
        media_mime_type="image/png",
    )

    first_result = process_incoming_message(db=db_session, message=message)
    duplicate_result = process_incoming_message(db=db_session, message=message)

    assert first_result.is_duplicate is False
    assert duplicate_result.is_duplicate is True
    assert duplicate_result.message.id == first_result.message.id
    assert downloads == ["duplicate-media"]
    assert (
        db_session.query(ComplaintMessage)
        .filter_by(whatsapp_message_id=message.whatsapp_message_id)
        .count()
        == 1
    )


def test_media_download_failure_preserves_existing_draft(
    db_session,
    monkeypatch,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            whatsapp_message_id="wamid.preexisting-draft",
            text_content="Water is leaking on Main Road.",
        ),
    )

    async def fail_download(media_id, *, expected_mime_type):
        raise WhatsAppMediaDownloadError("Mock media download failure")

    monkeypatch.setattr(
        processing_service,
        "download_whatsapp_media",
        fail_download,
    )
    media_message = create_incoming_message(
        MessageKind.IMAGE,
        whatsapp_message_id="wamid.failed-image",
        media_id="failed-media",
        media_mime_type="image/jpeg",
    )

    with pytest.raises(
        WhatsAppMediaDownloadError,
        match="Mock media download failure",
    ):
        process_incoming_message(db=db_session, message=media_message)

    db_session.expire_all()
    existing_draft = db_session.get(Complaint, first_result.complaint.id)

    assert existing_draft is not None
    assert existing_draft.status == ComplaintStatus.DRAFT
    assert existing_draft.conversation_state == ConversationState.ASKING_FOR_MORE_INFO
    assert (
        db_session.query(ComplaintMessage)
        .filter_by(whatsapp_message_id=media_message.whatsapp_message_id)
        .count()
        == 0
    )
    assert (
        db_session.query(OutboundWhatsAppMessage)
        .filter(OutboundWhatsAppMessage.inbound_message_id == first_result.message.id)
        .count()
        == 1
    )


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
            reply_id="complaint_add_more",
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
    assert saved_message.interactive_reply_id == "complaint_add_more"
    assert saved_message.interactive_reply_title == "Yes"
    assert saved_message.text_content is None


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
    assert complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO
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
    assert result.complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO

    assert result.message.text_content == ("There is a water leak on my street")

    assert result.message.complaint_id == result.complaint.id


def test_greeting_starts_new_conversation_without_complaint_details(
    db_session,
):
    result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="Hi",
            whatsapp_message_id="wamid.greeting-new",
        ),
    )

    assert result.event == ConversationEvent.MESSAGE_RECEIVED
    assert result.is_greeting is True
    assert result.complaint.status == ComplaintStatus.DRAFT
    assert result.complaint.conversation_state == ConversationState.AWAITING_COMPLAINT
    assert result.message.text_content is None
    greeting_reply = get_whatsapp_reply(result)
    assert greeting_reply.startswith(
        "Hello! 👋 Please describe the issue you want to report."
    )
    assert "photo/image" in greeting_reply
    assert "audio/voice message" in greeting_reply
    assert "location" in greeting_reply


def test_greeting_preserves_active_draft_and_conversation_state(
    db_session,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.greeting-active-details",
        ),
    )

    greeting_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="Hey!",
            whatsapp_message_id="wamid.greeting-active",
        ),
    )

    assert greeting_result.is_greeting is True
    assert greeting_result.complaint.id == first_result.complaint.id
    assert greeting_result.complaint.status == ComplaintStatus.DRAFT
    assert (
        greeting_result.complaint.conversation_state
        == ConversationState.ASKING_FOR_MORE_INFO
    )
    assert greeting_result.message.text_content is None
    assert get_whatsapp_reply(greeting_result).startswith(
        "Hello! Please continue sharing the details of your complaint."
    )

    yes_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="Yes",
            whatsapp_message_id="wamid.greeting-active-yes",
        ),
    )
    additional_info_greeting = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="Hi",
            whatsapp_message_id="wamid.greeting-awaiting-additional",
        ),
    )

    assert (
        yes_result.complaint.conversation_state
        == ConversationState.AWAITING_ADDITIONAL_INFO
    )
    assert additional_info_greeting.complaint.id == first_result.complaint.id
    assert (
        additional_info_greeting.complaint.conversation_state
        == ConversationState.AWAITING_ADDITIONAL_INFO
    )
    assert additional_info_greeting.message.text_content is None
    assert get_whatsapp_reply(additional_info_greeting).startswith(
        "Hello! Please send the additional information about your complaint."
    )


def test_greeting_after_submission_starts_a_new_draft(
    db_session,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.greeting-submitted-details",
        ),
    )

    submission_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="No",
            whatsapp_message_id="wamid.greeting-submitted-no",
        ),
    )

    greeting_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type=MessageKind.TEXT,
            text_content="Hello",
            whatsapp_message_id="wamid.greeting-after-submit",
        ),
    )

    assert submission_result.submitted is True
    assert submission_result.complaint.status == ComplaintStatus.SUBMITTED
    assert greeting_result.is_greeting is True
    assert greeting_result.complaint.id != first_result.complaint.id
    assert greeting_result.complaint.status == ComplaintStatus.DRAFT
    assert (
        greeting_result.complaint.conversation_state
        == ConversationState.AWAITING_COMPLAINT
    )
    assert greeting_result.message.text_content is None
    assert get_whatsapp_reply(greeting_result).startswith(
        "Hello! 👋 Please describe the issue you want to report."
    )


def test_expired_draft_is_abandoned_and_history_is_retained(db_session):
    last_activity = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.expired-draft-original",
        ),
        now=last_activity,
    )
    original_message_id = first_result.message.id
    first_result.complaint.created_at = last_activity
    first_result.complaint.last_activity_at = last_activity
    db_session.flush()

    replacement_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="A new street light is broken",
            whatsapp_message_id="wamid.expired-draft-replacement",
        ),
        now=last_activity + timedelta(hours=24),
    )

    assert first_result.complaint.status == ComplaintStatus.ABANDONED
    assert replacement_result.complaint.id != first_result.complaint.id
    assert replacement_result.complaint.status == ComplaintStatus.DRAFT
    assert replacement_result.complaint.last_activity_at == (
        last_activity + timedelta(hours=24)
    )
    assert (
        db_session.get(ComplaintMessage, original_message_id).complaint_id
        == first_result.complaint.id
    )


def test_greeting_does_not_refresh_active_draft_activity(db_session):
    last_activity = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.greeting-expiry-original",
        ),
        now=last_activity,
    )
    first_result.complaint.created_at = last_activity
    first_result.complaint.last_activity_at = last_activity
    db_session.flush()

    greeting_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Hi",
            whatsapp_message_id="wamid.greeting-before-expiry",
        ),
        now=last_activity + timedelta(hours=23),
    )

    assert greeting_result.complaint.id == first_result.complaint.id
    assert greeting_result.complaint.status == ComplaintStatus.DRAFT
    assert greeting_result.complaint.last_activity_at == last_activity
    assert greeting_result.message.text_content is None


def test_greeting_after_expiry_starts_a_fresh_draft(db_session):
    last_activity = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.expired-greeting-original",
        ),
        now=last_activity,
    )
    first_result.complaint.created_at = last_activity
    first_result.complaint.last_activity_at = last_activity
    db_session.flush()

    greeting_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Hello",
            whatsapp_message_id="wamid.expired-greeting-new",
        ),
        now=last_activity + timedelta(hours=24),
    )

    assert first_result.complaint.status == ComplaintStatus.ABANDONED
    assert greeting_result.complaint.id != first_result.complaint.id
    assert greeting_result.complaint.status == ComplaintStatus.DRAFT
    assert greeting_result.complaint.conversation_state == (
        ConversationState.AWAITING_COMPLAINT
    )
    assert greeting_result.complaint.last_activity_at is None
    assert greeting_result.message.text_content is None
    assert get_whatsapp_reply(greeting_result).startswith(
        "Hello! 👋 Please describe the issue you want to report."
    )


def test_activity_timestamp_updates_only_for_meaningful_messages(db_session):
    first_activity = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.activity-original",
        ),
        now=first_activity,
    )
    first_result.complaint.created_at = first_activity
    db_session.flush()

    yes_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Yes",
            whatsapp_message_id="wamid.activity-command",
        ),
        now=first_activity + timedelta(hours=1),
    )
    yes_activity = yes_result.complaint.last_activity_at
    greeting_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Hey",
            whatsapp_message_id="wamid.activity-greeting",
        ),
        now=first_activity + timedelta(hours=2),
    )
    greeting_activity = greeting_result.complaint.last_activity_at
    details_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="The leak is beside the park entrance.",
            whatsapp_message_id="wamid.activity-details",
        ),
        now=first_activity + timedelta(hours=3),
    )

    assert yes_activity == first_activity
    assert greeting_activity == first_activity
    assert details_result.complaint.last_activity_at == (
        first_activity + timedelta(hours=3)
    )
    assert details_result.complaint.last_activity_at.tzinfo is not None
    assert details_result.complaint.last_activity_at.astimezone(
        UTC
    ).utcoffset() == timedelta(0)


def test_duplicate_webhook_does_not_refresh_activity_or_create_complaint(
    db_session,
):
    last_activity = datetime(2026, 1, 1, tzinfo=UTC)
    message = create_incoming_message(
        MessageKind.TEXT,
        text_content="There is a water leak on my street",
        whatsapp_message_id="wamid.activity-duplicate",
    )
    first_result = process_incoming_message(
        db=db_session,
        message=message,
        now=last_activity,
    )
    first_result.complaint.created_at = last_activity
    first_result.complaint.last_activity_at = last_activity
    db_session.flush()

    duplicate_result = process_incoming_message(
        db=db_session,
        message=message,
        now=last_activity + timedelta(hours=25),
    )

    assert duplicate_result.is_duplicate is True
    assert duplicate_result.complaint.id == first_result.complaint.id
    assert duplicate_result.complaint.status == ComplaintStatus.DRAFT
    assert duplicate_result.complaint.last_activity_at == last_activity
    assert (
        db_session.query(Complaint)
        .filter(Complaint.user_id == first_result.user.id)
        .count()
        == 1
    )


def test_expiry_for_one_user_does_not_affect_another_users_draft(db_session):
    last_activity = datetime(2026, 1, 1, tzinfo=UTC)
    first_user_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Water is leaking on my street.",
            whatsapp_number="919876543210",
            whatsapp_message_id="wamid.user-one-original",
        ),
        now=last_activity,
    )
    second_user_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="The road has a pothole.",
            whatsapp_number="919876543211",
            whatsapp_message_id="wamid.user-two-original",
        ),
        now=last_activity,
    )
    for result in (first_user_result, second_user_result):
        result.complaint.created_at = last_activity
        result.complaint.last_activity_at = last_activity
    db_session.flush()

    replacement_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="A new issue for the first user.",
            whatsapp_number="919876543210",
            whatsapp_message_id="wamid.user-one-after-expiry",
        ),
        now=last_activity + timedelta(hours=24),
    )

    assert first_user_result.complaint.status == ComplaintStatus.ABANDONED
    assert second_user_result.complaint.status == ComplaintStatus.DRAFT
    assert replacement_result.complaint.user_id == first_user_result.user.id
    assert replacement_result.complaint.id != first_user_result.complaint.id


@pytest.mark.parametrize(
    ("reply_text", "expected_event", "expected_state", "submitted"),
    [
        (
            "Yeah",
            ConversationEvent.YES,
            ConversationState.AWAITING_ADDITIONAL_INFO,
            False,
        ),
        (
            "yep",
            ConversationEvent.YES,
            ConversationState.AWAITING_ADDITIONAL_INFO,
            False,
        ),
        (
            "Sure!",
            ConversationEvent.YES,
            ConversationState.AWAITING_ADDITIONAL_INFO,
            False,
        ),
        (
            "Nope",
            ConversationEvent.NO,
            ConversationState.COMPLETED,
            True,
        ),
    ],
)
def test_common_typed_replies_work_in_add_more_decision_state(
    db_session,
    reply_text,
    expected_event,
    expected_state,
    submitted,
):
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="The streetlight is not working.",
            whatsapp_message_id=f"wamid.variant-details-{reply_text}",
        ),
        now=base_time,
    )
    reply_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content=reply_text,
            whatsapp_message_id=f"wamid.variant-reply-{reply_text}",
        ),
        now=base_time + timedelta(hours=1),
    )

    assert reply_result.event == expected_event
    assert reply_result.submitted is submitted
    assert reply_result.complaint.conversation_state == expected_state
    assert reply_result.complaint.last_activity_at == base_time
    assert reply_result.complaint.id == first_result.complaint.id


@pytest.mark.parametrize(
    "reply_text",
    ["Maybe", "Not sure!", " unsure ", "ok", "OK", "Okay", "okay"],
)
def test_ambiguous_short_reply_clarifies_without_changing_draft(
    db_session,
    reply_text,
):
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="The streetlight is not working.",
            whatsapp_message_id=f"wamid.ambiguous-details-{reply_text}",
        ),
        now=base_time,
    )
    ambiguous_message = create_incoming_message(
        MessageKind.TEXT,
        text_content=reply_text,
        whatsapp_message_id=f"wamid.ambiguous-reply-{reply_text}",
    )
    result = process_incoming_message(
        db=db_session,
        message=ambiguous_message,
        now=base_time + timedelta(hours=1),
    )

    assert result.is_ambiguous_reply is True
    assert result.event == ConversationEvent.MESSAGE_RECEIVED
    assert result.submitted is False
    assert result.complaint.id == first_result.complaint.id
    assert result.complaint.status == ComplaintStatus.DRAFT
    assert result.complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO
    assert result.complaint.last_activity_at == base_time
    assert result.message.message_type == MessageType.OTHER
    assert result.message.text_content == reply_text

    reply = get_whatsapp_reply(result)
    assert isinstance(reply, InteractiveWhatsAppReply)
    assert (
        "Would you like to add more details, or submit your complaint? "
        "Please choose one."
    ) in reply.body
    assert "add more details" in reply.body
    assert "submit your complaint" in reply.body
    assert reply.buttons == (
        {"id": "complaint_add_more", "title": "Yes, add details"},
        {"id": "complaint_submit", "title": "No, submit"},
    )

    duplicate_result = process_incoming_message(
        db=db_session,
        message=ambiguous_message,
        now=base_time + timedelta(hours=2),
    )

    assert duplicate_result.is_duplicate is True
    assert duplicate_result.complaint.last_activity_at == base_time
    assert (
        db_session.query(OutboundWhatsAppMessage)
        .filter(OutboundWhatsAppMessage.complaint_id == first_result.complaint.id)
        .count()
        == 2
    )


def test_maybe_is_complaint_detail_when_not_awaiting_add_more_decision(db_session):
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Maybe the road is blocked.",
            whatsapp_message_id="wamid.maybe-complaint-detail",
        ),
        now=base_time,
    )

    assert result.is_ambiguous_reply is False
    assert result.event == ConversationEvent.MESSAGE_RECEIVED
    assert result.message.message_type == MessageType.TEXT
    assert result.message.text_content == "Maybe the road is blocked."
    assert result.complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO
    assert result.complaint.last_activity_at == base_time


def test_ordinary_complaint_text_is_not_ambiguous_while_asking_for_more(
    db_session,
):
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Water is leaking on the main road.",
            whatsapp_message_id="wamid.ordinary-details-first",
        ),
        now=base_time,
    )

    result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="The streetlight is not working.",
            whatsapp_message_id="wamid.ordinary-details-second",
        ),
        now=base_time + timedelta(hours=1),
    )

    assert result.is_ambiguous_reply is False
    assert result.message.message_type == MessageType.TEXT
    assert result.message.text_content == "The streetlight is not working."
    assert result.complaint.last_activity_at == base_time + timedelta(hours=1)
    assert result.complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO


@pytest.mark.parametrize(
    ("message_type", "expected_message_type"),
    [
        (MessageKind.TEXT, MessageType.TEXT),
        (MessageKind.IMAGE, MessageType.IMAGE),
        (MessageKind.AUDIO, MessageType.AUDIO),
        (MessageKind.LOCATION, MessageType.LOCATION),
    ],
)
def test_add_more_flow_still_accepts_supported_detail_types(
    db_session,
    monkeypatch,
    message_type,
    expected_message_type,
):
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="Water is leaking on the main road.",
            whatsapp_message_id=f"wamid.add-more-first-{message_type.value}",
        ),
        now=base_time,
    )
    process_incoming_message(
        db=db_session,
        message=create_interactive_button_message(
            "complaint_add_more",
            f"wamid.add-more-button-{message_type.value}",
        ),
        now=base_time + timedelta(hours=1),
    )

    if message_type in {MessageKind.IMAGE, MessageKind.AUDIO}:

        async def download_media(media_id, *, expected_mime_type):
            return WhatsAppMediaDownload(
                storage_key=f"{'c' * 32}.media",
                media_id=media_id,
                mime_type=expected_mime_type,
                size_bytes=64,
                sha256="c" * 64,
            )

        monkeypatch.setattr(
            processing_service,
            "download_whatsapp_media",
            download_media,
        )

    details = {
        MessageKind.TEXT: {
            "text_content": "The leak is beside the park entrance.",
        },
        MessageKind.IMAGE: {
            "media_id": "add-more-image",
            "media_mime_type": "image/jpeg",
        },
        MessageKind.AUDIO: {
            "media_id": "add-more-audio",
            "media_mime_type": "audio/ogg",
        },
        MessageKind.LOCATION: {
            "latitude": Decimal("17.6599"),
            "longitude": Decimal("75.9064"),
        },
    }[message_type]
    result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type,
            whatsapp_message_id=f"wamid.add-more-detail-{message_type.value}",
            **details,
        ),
        now=base_time + timedelta(hours=2),
    )

    assert result.complaint.id == first_result.complaint.id
    assert result.complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO
    assert result.message.message_type == expected_message_type
    assert result.complaint.last_activity_at == base_time + timedelta(hours=2)


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

    assert details_result.message.text_content == ("The leak is near the main road")


@pytest.mark.parametrize(
    ("message_type", "message_fields", "expected_message_type"),
    [
        (
            MessageKind.TEXT,
            {"text_content": "The leak is near the main road"},
            MessageType.TEXT,
        ),
        (
            MessageKind.IMAGE,
            {"media_id": "additional-image"},
            MessageType.IMAGE,
        ),
        (
            MessageKind.AUDIO,
            {"media_id": "additional-audio"},
            MessageType.AUDIO,
        ),
        (
            MessageKind.LOCATION,
            {
                "latitude": Decimal("17.6599"),
                "longitude": Decimal("75.9064"),
                "location_name": "Solapur",
                "location_address": "Solapur, Maharashtra",
            },
            MessageType.LOCATION,
        ),
    ],
)
def test_add_more_button_accepts_followup_details(
    db_session,
    monkeypatch,
    message_type,
    message_fields,
    expected_message_type,
):
    async def download_media(media_id, *, expected_mime_type):
        extension = ".jpg" if expected_mime_type == "image/jpeg" else ".ogg"
        return WhatsAppMediaDownload(
            storage_key=f"{'c' * 32}{extension}",
            media_id=media_id,
            mime_type=expected_mime_type or "audio/ogg",
            size_bytes=32,
            sha256="c" * 64,
        )

    monkeypatch.setattr(
        processing_service,
        "download_whatsapp_media",
        download_media,
    )

    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.button-first-details",
        ),
    )

    add_more_result = process_incoming_message(
        db=db_session,
        message=create_interactive_button_message(
            "complaint_add_more",
            "wamid.button-add-more",
        ),
    )

    assert add_more_result.event == ConversationEvent.YES
    assert add_more_result.complaint.id == first_result.complaint.id
    assert (
        add_more_result.complaint.conversation_state
        == ConversationState.AWAITING_ADDITIONAL_INFO
    )
    assert add_more_result.message.text_content is None
    assert add_more_result.message.message_type == MessageType.OTHER
    add_more_reply = get_whatsapp_reply(add_more_result)
    assert "Please send the additional information" in add_more_reply
    assert "location" in add_more_reply

    details_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            message_type,
            whatsapp_message_id="wamid.button-additional-details",
            **message_fields,
        ),
    )

    assert details_result.event == ConversationEvent.MESSAGE_RECEIVED
    assert details_result.complaint.id == first_result.complaint.id
    assert details_result.message.message_type == expected_message_type
    assert (
        details_result.complaint.conversation_state
        == ConversationState.ASKING_FOR_MORE_INFO
    )
    if expected_message_type == MessageType.LOCATION:
        assert details_result.message.latitude == Decimal("17.6599")
        assert details_result.message.longitude == Decimal("75.9064")
        assert details_result.message.location_name == "Solapur"
        assert details_result.message.location_address == ("Solapur, Maharashtra")


def test_submit_button_submits_complaint_with_meaningful_details(
    db_session,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.button-submit-details",
        ),
    )

    submit_result = process_incoming_message(
        db=db_session,
        message=create_interactive_button_message(
            "complaint_submit",
            "wamid.button-submit",
        ),
    )

    assert submit_result.event == ConversationEvent.NO
    assert submit_result.submitted is True
    assert submit_result.complaint.id == first_result.complaint.id
    assert submit_result.complaint.status == ComplaintStatus.SUBMITTED
    assert submit_result.complaint.conversation_state == ConversationState.COMPLETED
    assert submit_result.message.text_content is None
    assert submit_result.message.message_type == MessageType.OTHER
    assert "registered successfully" in get_whatsapp_reply(submit_result)


def test_submit_button_without_meaningful_details_keeps_draft(
    db_session,
):
    empty_draft_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="No",
            whatsapp_message_id="wamid.button-empty-draft-no",
        ),
    )
    add_more_result = process_incoming_message(
        db=db_session,
        message=create_interactive_button_message(
            "complaint_add_more",
            "wamid.button-empty-add-more",
        ),
    )

    assert empty_draft_result.needs_more_information is True
    assert add_more_result.event == ConversationEvent.YES
    assert (
        add_more_result.complaint.conversation_state
        == ConversationState.AWAITING_ADDITIONAL_INFO
    )
    assert add_more_result.message.text_content is None

    result = process_incoming_message(
        db=db_session,
        message=create_interactive_button_message(
            "complaint_submit",
            "wamid.button-submit-empty",
        ),
    )

    assert result.event == ConversationEvent.NO
    assert result.submitted is False
    assert result.needs_more_information is True
    assert result.complaint.status == ComplaintStatus.DRAFT
    assert result.complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO
    assert result.message.text_content is None
    assert result.message.message_type == MessageType.OTHER
    assert "more information" in get_whatsapp_reply(result)


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
    assert no_result.complaint.conversation_state == ConversationState.COMPLETED

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
    assert result.complaint.conversation_state == ConversationState.ASKING_FOR_MORE_INFO
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
        .filter(ComplaintMessage.whatsapp_message_id == "wamid.duplicate-webhook")
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


def test_duplicate_submit_button_webhook_does_not_resubmit(
    db_session,
):
    first_result = process_incoming_message(
        db=db_session,
        message=create_incoming_message(
            MessageKind.TEXT,
            text_content="There is a water leak on my street",
            whatsapp_message_id="wamid.duplicate-button-details",
        ),
    )
    submit_message = create_interactive_button_message(
        "complaint_submit",
        "wamid.duplicate-button-submit",
    )

    submit_result = process_incoming_message(
        db=db_session,
        message=submit_message,
    )
    duplicate_result = process_incoming_message(
        db=db_session,
        message=submit_message,
    )

    assert submit_result.submitted is True
    assert duplicate_result.is_duplicate is True
    assert duplicate_result.submitted is True
    assert duplicate_result.complaint.id == first_result.complaint.id
    assert duplicate_result.message.id == submit_result.message.id

    complaint_count = (
        db_session.query(Complaint)
        .filter(Complaint.user_id == first_result.user.id)
        .count()
    )
    outbox_count = (
        db_session.query(OutboundWhatsAppMessage)
        .filter(OutboundWhatsAppMessage.inbound_message_id == submit_result.message.id)
        .count()
    )

    assert complaint_count == 1
    assert outbox_count == 1


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
        .filter(OutboundWhatsAppMessage.inbound_message_id == result.message.id)
        .one()
    )

    expected_reply = get_whatsapp_reply(result)
    db_session.expire(outbound_message, ["interactive_buttons"])

    assert isinstance(expected_reply, InteractiveWhatsAppReply)
    assert outbound_message.inbound_message_id == result.message.id
    assert outbound_message.complaint_id == result.complaint.id
    assert outbound_message.recipient == message.whatsapp_number
    assert outbound_message.body == expected_reply.body
    assert outbound_message.interactive_buttons == list(expected_reply.buttons)
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
        .filter(OutboundWhatsAppMessage.inbound_message_id == first_result.message.id)
        .all()
    )

    assert first_result.is_duplicate is False
    assert duplicate_result.is_duplicate is True
    assert duplicate_result.message.id == first_result.message.id

    # Only the original inbound message should have an outbox record.
    assert len(outbound_messages) == 1

    outbound_message = outbound_messages[0]

    assert outbound_message.status == OutboundMessageStatus.PENDING
    expected_reply = get_whatsapp_reply(first_result)
    assert isinstance(expected_reply, InteractiveWhatsAppReply)
    assert outbound_message.body == expected_reply.body
    assert outbound_message.interactive_buttons == list(expected_reply.buttons)
