from enum import Enum

from app.models.complaint import ConversationState
from app.schemas.whatsapp_message import InteractiveReply


class ConversationEvent(str, Enum):
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    YES = "YES"
    NO = "NO"


def _normalize_reply(text: str) -> str:
    return text.strip().casefold().strip(" \t\n\r.,!?")


def classify_reply(
    text: str,
    *,
    allow_common_variants: bool = False,
) -> ConversationEvent:
    """Identify a simple Yes or No response."""

    normalized_text = _normalize_reply(text)
    affirmative_replies = {"yes", "y"}
    negative_replies = {"no", "n"}

    if allow_common_variants:
        affirmative_replies.update({"yeah", "yep", "sure", "ok", "okay"})
        negative_replies.add("nope")

    if normalized_text in affirmative_replies:
        return ConversationEvent.YES

    if normalized_text in negative_replies:
        return ConversationEvent.NO

    return ConversationEvent.MESSAGE_RECEIVED


def is_ambiguous_short_reply(text: str) -> bool:
    return _normalize_reply(text) in {"maybe", "not sure", "unsure"}


def is_greeting_reply(text: str) -> bool:
    """Identify common greetings without assigning them a conversation event."""

    normalized_text = _normalize_reply(text)
    return normalized_text in {"hi", "hello", "hey"}


def classify_interactive_reply(
    reply: InteractiveReply,
) -> ConversationEvent:
    """Classify a WhatsApp interactive reply using its stable ID."""

    if reply.reply_id == "complaint_add_more":
        return ConversationEvent.YES

    if reply.reply_id == "complaint_submit":
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

    raise ValueError(f"Invalid event {event.value} for state {current_state.value}")
