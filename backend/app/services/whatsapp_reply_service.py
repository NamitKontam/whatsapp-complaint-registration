
from app.models.complaint import ConversationState
from app.services.complaint_processing_service import (
    ComplaintProcessingResult,
)
from app.services.conversation_service import ConversationEvent


def get_whatsapp_reply(
    result: ComplaintProcessingResult,
) -> str | None:
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
        return (
            "We need more information to register your complaint. "
            "Please describe the issue in detail."
        )

    if result.is_greeting:
        if (
            result.complaint.conversation_state
            == ConversationState.AWAITING_ADDITIONAL_INFO
        ):
            return (
                "Hello! Please send the additional information "
                "about your complaint."
            )

        if (
            result.complaint.conversation_state
            == ConversationState.ASKING_FOR_MORE_INFO
        ):
            return (
                "Hello! Please continue sharing the details "
                "of your complaint."
            )

        return "Hello! Please describe the issue you want to report."

    if result.event == ConversationEvent.YES:
        return (
            "Please send the additional information about your complaint."
        )

    if result.event == ConversationEvent.NO:
        return (
            "Your complaint could not be registered yet. "
            "Please provide more details about the issue."
        )

    return (
        "Thank you for your message. "
        "Please continue sharing the details of your complaint."
    )
