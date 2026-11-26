from types import SimpleNamespace
from unittest.mock import Mock

from app.models.complaint_message import MessageType
from app.services import whatsapp_reply_service as reply_service
from app.services.complaint_processing_service import (
    ComplaintProcessingResult,
)
from app.services.conversation_service import ConversationEvent
from app.services.whatsapp_media_service import MediaUploadGuidance
from app.services.whatsapp_reply_service import (
    InteractiveWhatsAppReply,
    get_whatsapp_reply,
)


def make_result(
    *,
    event: ConversationEvent | None = None,
    is_duplicate: bool = False,
    submitted: bool = False,
    needs_more_information: bool = False,
    complaint_number: str | None = "CMP-0001",
    message=None,
) -> ComplaintProcessingResult:
    """Build a lightweight processing result for reply tests."""

    return ComplaintProcessingResult(
        user=Mock(),
        complaint=SimpleNamespace(
            complaint_number=complaint_number,
        ),
        message=message or Mock(),
        event=event,
        is_duplicate=is_duplicate,
        submitted=submitted,
        needs_more_information=needs_more_information,
    )


def test_duplicate_message_returns_no_reply():
    result = make_result(is_duplicate=True)

    assert get_whatsapp_reply(result) is None


def test_submitted_complaint_returns_confirmation():
    result = make_result(submitted=True)

    reply = get_whatsapp_reply(result)

    assert "registered successfully" in reply
    assert "CMP-0001" in reply


def test_missing_information_returns_request_for_details():
    result = make_result(needs_more_information=True)

    reply = get_whatsapp_reply(result)

    assert "more information" in reply
    assert "describe the issue" in reply


def test_yes_reply_requests_additional_information():
    result = make_result(event=ConversationEvent.YES)

    reply = get_whatsapp_reply(result)

    assert "additional information" in reply


def test_meaningful_message_returns_add_more_interactive_reply():
    result = make_result(
        event=ConversationEvent.MESSAGE_RECEIVED,
        message=SimpleNamespace(
            message_type=MessageType.TEXT,
            text_content="Water is leaking on the main road.",
        ),
    )

    reply = get_whatsapp_reply(result)

    assert isinstance(reply, InteractiveWhatsAppReply)
    assert reply.body.startswith(
        "Would you like to add more details to your complaint?"
    )
    assert "photo/image" in reply.body
    assert "audio/voice message" in reply.body
    assert "location" in reply.body
    assert reply.buttons == (
        {"id": "complaint_add_more", "title": "Yes, add details"},
        {"id": "complaint_submit", "title": "No, submit"},
    )


def test_reply_guidance_uses_configured_media_types_and_size(monkeypatch):
    guidance = MediaUploadGuidance(
        image_types=("configured image",),
        audio_types=("configured audio",),
        max_size_bytes=3_000_000,
    )
    monkeypatch.setattr(
        reply_service,
        "get_media_upload_guidance",
        lambda settings: guidance,
    )
    result = make_result(
        event=ConversationEvent.MESSAGE_RECEIVED,
        message=SimpleNamespace(
            message_type=MessageType.TEXT,
            text_content="Water is leaking on the main road.",
        ),
    )

    reply = get_whatsapp_reply(result)

    assert isinstance(reply, InteractiveWhatsAppReply)
    assert "configured image" in reply.body
    assert "configured audio" in reply.body
    assert "3 MB per file" in reply.body


def test_no_reply_requests_complaint_details():
    result = make_result(event=ConversationEvent.NO)

    reply = get_whatsapp_reply(result)

    assert "provide more details" in reply


def test_ordinary_message_returns_acknowledgement():
    result = make_result(event=ConversationEvent.MESSAGE_RECEIVED)

    reply = get_whatsapp_reply(result)

    assert "Thank you for your message" in reply
    assert "complaint" in reply
