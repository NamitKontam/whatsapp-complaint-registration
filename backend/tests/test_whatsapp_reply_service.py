
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.complaint_processing_service import (
    ComplaintProcessingResult,
)
from app.services.conversation_service import ConversationEvent
from app.services.whatsapp_reply_service import get_whatsapp_reply


def make_result(
    *,
    event: ConversationEvent | None = None,
    is_duplicate: bool = False,
    submitted: bool = False,
    needs_more_information: bool = False,
    complaint_number: str | None = "CMP-0001",
) -> ComplaintProcessingResult:
    """Build a lightweight processing result for reply tests."""

    return ComplaintProcessingResult(
        user=Mock(),
        complaint=SimpleNamespace(
            complaint_number=complaint_number,
        ),
        message=Mock(),
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


def test_no_reply_requests_complaint_details():
    result = make_result(event=ConversationEvent.NO)

    reply = get_whatsapp_reply(result)

    assert "provide more details" in reply


def test_ordinary_message_returns_acknowledgement():
    result = make_result(event=ConversationEvent.MESSAGE_RECEIVED)

    reply = get_whatsapp_reply(result)

    assert "Thank you for your message" in reply
    assert "complaint" in reply
