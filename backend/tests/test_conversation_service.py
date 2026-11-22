
from app.models.complaint import ConversationState
from app.services.conversation_service import (
    ConversationEvent,
    classify_reply,
    get_next_conversation_state,
)


def main():
    # Test reply classification.
    print("Reply classification:")

    for reply in [
        "Yes",
        "y",
        "No",
        "n",
        "No water supply",
        "Yesterday there was leakage",
    ]:
        print(reply, "->", classify_reply(reply).value)

    # Test initial complaint.
    state = get_next_conversation_state(
        ConversationState.AWAITING_COMPLAINT,
        ConversationEvent.MESSAGE_RECEIVED,
    )

    print("\nAfter initial complaint:", state.value)

    # Test Yes.
    state = get_next_conversation_state(
        state,
        ConversationEvent.YES,
    )

    print("After Yes:", state.value)

    # Test additional information.
    state = get_next_conversation_state(
        state,
        ConversationEvent.MESSAGE_RECEIVED,
    )

    print("After additional information:", state.value)

    # Test No.
    state = get_next_conversation_state(
        state,
        ConversationEvent.NO,
    )

    print("After No:", state.value)


if __name__ == "__main__":
    main()
