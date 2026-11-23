
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.schemas.whatsapp_message import (
    IncomingWhatsAppMessage,
    InteractiveReply,
    InteractiveReplyType,
    MessageKind,
)
from pydantic import ValidationError


@pytest.fixture
def base_message():
    return {
        "whatsapp_number": "919876543210",
        "whatsapp_message_id": "wamid.test123",
        "timestamp": datetime.now(UTC),
    }


def test_valid_text_message(base_message):
    message = IncomingWhatsAppMessage(
        **base_message,
        message_type=MessageKind.TEXT,
        text_content="There is water leakage.",
    )

    assert message.message_type == MessageKind.TEXT
    assert message.text_content == "There is water leakage."


@pytest.mark.parametrize("message_type", [
    MessageKind.IMAGE,
    MessageKind.AUDIO,
])
def test_valid_media_message(base_message, message_type):
    message = IncomingWhatsAppMessage(
        **base_message,
        message_type=message_type,
        media_id="media123",
        mime_type="image/jpeg" if message_type == MessageKind.IMAGE else "audio/ogg",
    )

    assert message.media_id == "media123"


def test_media_message_requires_media_id(base_message):
    with pytest.raises(ValidationError):
        IncomingWhatsAppMessage(
            **base_message,
            message_type=MessageKind.IMAGE,
        )


def test_valid_location_message(base_message):
    message = IncomingWhatsAppMessage(
        **base_message,
        message_type=MessageKind.LOCATION,
        latitude=Decimal("17.6599"),
        longitude=Decimal("75.9064"),
    )

    assert message.latitude == Decimal("17.6599")


def test_location_requires_coordinates(base_message):
    with pytest.raises(ValidationError):
        IncomingWhatsAppMessage(
            **base_message,
            message_type=MessageKind.LOCATION,
            latitude=Decimal("17.6599"),
        )


@pytest.mark.parametrize("field,value", [
    ("latitude", Decimal(91)),
    ("longitude", Decimal(181)),
])
def test_invalid_coordinates(base_message, field, value):
    with pytest.raises(ValidationError):
        IncomingWhatsAppMessage(
            **base_message,
            message_type=MessageKind.LOCATION,
            latitude=value if field == "latitude" else Decimal(17),
            longitude=value if field == "longitude" else Decimal(75),
        )


def test_valid_interactive_reply(base_message):
    reply = InteractiveReply(
        reply_type=InteractiveReplyType.BUTTON,
        reply_id="yes",
        title="Yes",
    )

    message = IncomingWhatsAppMessage(
        **base_message,
        message_type=MessageKind.INTERACTIVE,
        interactive_reply=reply,
    )

    assert message.interactive_reply.reply_id == "yes"


def test_interactive_message_requires_reply(base_message):
    with pytest.raises(ValidationError):
        IncomingWhatsAppMessage(
            **base_message,
            message_type=MessageKind.INTERACTIVE,
        )


def test_naive_timestamp_is_rejected(base_message):
    base_message["timestamp"] = datetime.fromisoformat("2026-11-23T10:00:00")

    with pytest.raises(ValidationError):
        IncomingWhatsAppMessage(
            **base_message,
            message_type=MessageKind.TEXT,
            text_content="Hello",
        )


def test_unexpected_fields_are_rejected(base_message):
    with pytest.raises(ValidationError):
        IncomingWhatsAppMessage(
            **base_message,
            message_type=MessageKind.TEXT,
            text_content="Hello",
            unexpected_field="not allowed",
        )
