
import pytest

from app.models.complaint import ConversationState
from app.services.conversation_service import (
    ConversationEvent,
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
        ("No water supply", ConversationEvent.MESSAGE_RECEIVED),
        ("Yesterday there was leakage", ConversationEvent.MESSAGE_RECEIVED),
    ],
)
def test_classify_reply(reply, expected_event):
    assert classify_reply(reply) == expected_event


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
