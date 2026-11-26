from dataclasses import dataclass
from typing import TypedDict

from app.core.config import get_settings
from app.models.complaint import ConversationState
from app.models.complaint_message import MessageType
from app.services.complaint_processing_service import (
    ComplaintProcessingResult,
)
from app.services.conversation_service import ConversationEvent
from app.services.whatsapp_media_service import get_media_upload_guidance


class ReplyButton(TypedDict):
    id: str
    title: str


@dataclass(frozen=True)
class InteractiveWhatsAppReply:
    body: str
    buttons: tuple[ReplyButton, ...]


def _with_media_guidance(message: str) -> str:
    guidance = get_media_upload_guidance(get_settings())
    return (
        f"{message}\n\n"
        "You can provide text, a photo/image, an audio/voice message, "
        "or a location. "
        f"{guidance.format_message()}"
    )


def _current_message_has_meaningful_details(
    result: ComplaintProcessingResult,
) -> bool:
    if result.event != ConversationEvent.MESSAGE_RECEIVED:
        return False

    if result.is_greeting:
        return False

    message = result.message

    if message.message_type == MessageType.TEXT:
        return bool(message.text_content and message.text_content.strip())

    if message.message_type in {MessageType.IMAGE, MessageType.AUDIO}:
        return message.media_id is not None

    if message.message_type == MessageType.LOCATION:
        return message.latitude is not None and message.longitude is not None

    return False


def get_whatsapp_reply(
    result: ComplaintProcessingResult,
) -> str | InteractiveWhatsAppReply | None:
    """Choose the appropriate WhatsApp reply for a processed message."""

    if result.is_duplicate:
        return None

    if result.submitted:
        complaint_number = result.complaint.complaint_number

        return (
            "Your complaint has been registered successfully. "
            f"Your complaint number is {complaint_number}."
        )

    if result.needs_more_information:
        return _with_media_guidance(
            "We need more information to register your complaint. "
            "Please describe the issue in detail."
        )

    if result.is_greeting:
        if (
            result.complaint.conversation_state
            == ConversationState.AWAITING_ADDITIONAL_INFO
        ):
            return _with_media_guidance(
                "Hello! Please send the additional information about your complaint."
            )

        if (
            result.complaint.conversation_state
            == ConversationState.ASKING_FOR_MORE_INFO
        ):
            return _with_media_guidance(
                "Hello! Please continue sharing the details of your complaint."
            )

        return _with_media_guidance(
            "Hello! Please describe the issue you want to report."
        )

    if result.event == ConversationEvent.YES:
        return _with_media_guidance(
            "Please send the additional information about your complaint."
        )

    if result.event == ConversationEvent.NO:
        return _with_media_guidance(
            "Your complaint could not be registered yet. "
            "Please provide more details about the issue."
        )

    if _current_message_has_meaningful_details(result):
        return InteractiveWhatsAppReply(
            body=_with_media_guidance(
                "Would you like to add more details to your complaint?"
            ),
            buttons=(
                {
                    "id": "complaint_add_more",
                    "title": "Yes, add details",
                },
                {
                    "id": "complaint_submit",
                    "title": "No, submit",
                },
            ),
        )

    return _with_media_guidance(
        "Thank you for your message. "
        "Please continue sharing the details of your complaint."
    )
