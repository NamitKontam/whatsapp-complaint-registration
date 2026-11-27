import pytest

from app.models.complaint import ConversationState
from app.schemas.whatsapp_message import (
    InteractiveReply,
    InteractiveReplyType,
)
from app.services.conversation_service import (
    ConversationEvent,
    classify_interactive_reply,
    classify_reply,
    get_next_conversation_state,
)


@pytest.mark.parametrize(
    ("reply", "expected_event"),
    [
        ("Yes", ConversationEvent.YES),
        ("yes", ConversationEvent.YES),
        ("Y", ConversationEvent.YES),
        ("No", ConversationEvent.NO),
        ("no", ConversationEvent.NO),
        ("N", ConversationEvent.NO),
        ("hi", ConversationEvent.MESSAGE_RECEIVED),
        ("Hello!", ConversationEvent.MESSAGE_RECEIVED),
        (" hey ", ConversationEvent.MESSAGE_RECEIVED),
        ("No water supply", ConversationEvent.MESSAGE_RECEIVED),
        ("Yesterday there was leakage", ConversationEvent.MESSAGE_RECEIVED),
    ],
)
def test_classify_reply(reply, expected_event):
    assert classify_reply(reply) == expected_event


@pytest.mark.parametrize(
    ("reply", "expected_event"),
    [
        (" Yeah! ", ConversationEvent.YES),
        ("yep", ConversationEvent.YES),
        ("sure.", ConversationEvent.YES),
        ("nope", ConversationEvent.NO),
        ("ok", ConversationEvent.MESSAGE_RECEIVED),
        ("OK", ConversationEvent.MESSAGE_RECEIVED),
        ("Okay", ConversationEvent.MESSAGE_RECEIVED),
        ("okay!", ConversationEvent.MESSAGE_RECEIVED),
    ],
)
def test_classify_common_variants_when_asked_for_more_info(reply, expected_event):
    assert classify_reply(reply, allow_common_variants=True) == expected_event


@pytest.mark.parametrize(
    "reply",
    ["maybe", "Not sure!", " unsure ", "ok", "OK", "Okay", "okay"],
)
def test_identify_ambiguous_short_replies(reply):
    from app.services.conversation_service import is_ambiguous_short_reply

    assert is_ambiguous_short_reply(reply)


@pytest.mark.parametrize(
    ("current_state", "event", "expected_state"),
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
        (
            ConversationState.ASKING_FOR_MORE_INFO,
            ConversationEvent.MESSAGE_RECEIVED,
            ConversationState.ASKING_FOR_MORE_INFO,
        ),
    ],
)
def test_conversation_state_transitions(
    current_state,
    event,
    expected_state,
):
    result = get_next_conversation_state(current_state, event)

    assert result == expected_state


def test_invalid_transition_raises_error():
    with pytest.raises(ValueError):
        get_next_conversation_state(
            ConversationState.AWAITING_COMPLAINT,
            ConversationEvent.NO,
        )


@pytest.mark.parametrize(
    ("reply_id", "expected_event"),
    [
        ("complaint_add_more", ConversationEvent.YES),
        ("complaint_submit", ConversationEvent.NO),
        ("unknown_option", ConversationEvent.MESSAGE_RECEIVED),
    ],
)
def test_classify_interactive_reply(reply_id, expected_event):
    reply = InteractiveReply(
        reply_type=InteractiveReplyType.BUTTON,
        reply_id=reply_id,
        title="Some visible title",
    )

    assert classify_interactive_reply(reply) == expected_event
