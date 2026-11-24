
from enum import Enum

from app.models.complaint import ConversationState
from app.schemas.whatsapp_message import InteractiveReply


class ConversationEvent(str, Enum):
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    YES = "YES"
    NO = "NO"


def classify_reply(text: str) -> ConversationEvent:
    """Identify a simple Yes or No response."""

    normalized_text = text.strip().casefold().strip(" \t\n\r.,!?")

    if normalized_text in {"yes", "y"}:
        return ConversationEvent.YES

    if normalized_text in {"no", "n"}:
        return ConversationEvent.NO

    return ConversationEvent.MESSAGE_RECEIVED


def get_next_conversation_state(
    current_state: ConversationState,
    event: ConversationEvent,
) -> ConversationState:
    """Determine the next conversation state."""

    if current_state == ConversationState.AWAITING_COMPLAINT:
        if event == ConversationEvent.MESSAGE_RECEIVED:
            return ConversationState.ASKING_FOR_MORE_INFO

    elif current_state == ConversationState.ASKING_FOR_MORE_INFO:
        if event == ConversationEvent.YES:
            return ConversationState.AWAITING_ADDITIONAL_INFO

        if event == ConversationEvent.NO:
            return ConversationState.COMPLETED

        if event == ConversationEvent.MESSAGE_RECEIVED:
            return ConversationState.ASKING_FOR_MORE_INFO

    elif (
        current_state == ConversationState.AWAITING_ADDITIONAL_INFO
        and event == ConversationEvent.MESSAGE_RECEIVED
    ):
        return ConversationState.ASKING_FOR_MORE_INFO

    raise ValueError(
        f"Invalid event {event.value} for state {current_state.value}"
    )


def classify_interactive_reply(
    reply: InteractiveReply,
) -> ConversationEvent:
    """Classify a WhatsApp interactive reply using its stable ID."""

    if reply.reply_id == "complaint_yes":
        return ConversationEvent.YES

    if reply.reply_id == "complaint_no":
        return ConversationEvent.NO

    return ConversationEvent.MESSAGE_RECEIVED