from decimal import Decimal
import pytest
from pydantic import BaseModel

from app.services.whatsapp_payload_service import (
    NormalizedMessage,
    NormalizedMessageType,
    NormalizedStatus,
    ParsedWebhookPayload,
    PayloadValidationError,
    UnsupportedMessageTypeError,
    extract_messages,
    extract_single_message,
    extract_single_status,
    parse_webhook_payload,
)


def _build_meta_payload(messages=None, statuses=None):
    """Helper to generate a realistic Meta WhatsApp Cloud API webhook structure."""
    value = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "15550253483",
            "phone_number_id": "274110702445115",
        },
    }
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100200300",
                "changes": [
                    {
                        "field": "messages",
                        "value": value,
                    }
                ],
            }
        ],
    }


# ==========================================
# 1. Valid Text Message Tests
# ==========================================


def test_parse_valid_text_message():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.text001",
        "timestamp": "1732345678",
        "type": "text",
        "text": {"body": "There is water leakage near my house."},
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)

    assert len(parsed.messages) == 1
    assert len(parsed.statuses) == 0

    msg = parsed.messages[0]
    assert isinstance(msg, NormalizedMessage)
    assert isinstance(msg, BaseModel)
    assert msg.sender == "919876543210"
    assert msg.whatsapp_message_id == "wamid.text001"
    assert msg.timestamp == "1732345678"
    assert msg.message_type == NormalizedMessageType.TEXT
    assert msg.text_content == "There is water leakage near my house."
    assert msg.media_id is None
    assert msg.latitude is None
    assert msg.longitude is None

    # Check Pydantic model_dump() and to_dict() contract
    dumped = msg.model_dump()
    assert dumped["sender"] == "919876543210"
    msg_dict = msg.to_dict()
    assert msg_dict["message_type"] == "TEXT"
    assert msg_dict["text_content"] == "There is water leakage near my house."


# ==========================================
# 2. Valid Image Message Tests
# ==========================================


def test_parse_valid_image_with_caption():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.img001",
        "timestamp": "1732345679",
        "type": "image",
        "image": {
            "id": "media_img_999",
            "mime_type": "image/jpeg",
            "caption": "Photo of broken pipe",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert msg.message_type == NormalizedMessageType.IMAGE
    assert msg.media_id == "media_img_999"
    assert msg.media_mime_type == "image/jpeg"
    assert msg.caption == "Photo of broken pipe"


def test_parse_valid_image_without_caption():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.img002",
        "timestamp": "1732345680",
        "type": "image",
        "image": {
            "id": "media_img_888",
            "mime_type": "image/png",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert msg.message_type == NormalizedMessageType.IMAGE
    assert msg.media_id == "media_img_888"
    assert msg.media_mime_type == "image/png"
    assert msg.caption is None


# ==========================================
# 3. Valid Audio Message Tests
# ==========================================


def test_parse_valid_audio_message():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.audio001",
        "timestamp": "1732345681",
        "type": "audio",
        "audio": {
            "id": "media_audio_777",
            "mime_type": "audio/ogg; codecs=opus",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert msg.message_type == NormalizedMessageType.AUDIO
    assert msg.media_id == "media_audio_777"
    assert msg.media_mime_type == "audio/ogg; codecs=opus"


def test_parse_valid_voice_message():
    """WhatsApp voice notes are often labeled type: voice."""
    raw_message = {
        "from": "919876543210",
        "id": "wamid.voice001",
        "timestamp": "1732345682",
        "type": "voice",
        "voice": {
            "id": "media_voice_666",
            "mime_type": "audio/ogg",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert msg.message_type == NormalizedMessageType.AUDIO
    assert msg.media_id == "media_voice_666"
    assert msg.media_mime_type == "audio/ogg"


# ==========================================
# 4. Valid Location Message Tests
# ==========================================


def test_parse_valid_location_message():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.loc001",
        "timestamp": "1732345683",
        "type": "location",
        "location": {
            "latitude": 12.9716,
            "longitude": 77.5946,
            "name": "Town Hall",
            "address": "MG Road, Bengaluru",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert msg.message_type == NormalizedMessageType.LOCATION
    assert msg.latitude == Decimal("12.9716")
    assert msg.longitude == Decimal("77.5946")
    assert msg.location_name == "Town Hall"
    assert msg.location_address == "MG Road, Bengaluru"


# ==========================================
# 5. Missing Optional Fields
# ==========================================


def test_parse_location_missing_optional_name_and_address():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.loc002",
        "timestamp": "1732345684",
        "type": "location",
        "location": {
            "latitude": "28.6139",
            "longitude": "77.2090",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    msg = parsed.messages[0]

    assert msg.latitude == Decimal("28.6139")
    assert msg.longitude == Decimal("77.2090")
    assert msg.location_name is None
    assert msg.location_address is None


# ==========================================
# 6. Missing Required Fields
# ==========================================


@pytest.mark.parametrize(
    "missing_key",
    ["from", "id", "timestamp", "type"],
)
def test_missing_top_level_required_fields(missing_key):
    raw_message = {
        "from": "919876543210",
        "id": "wamid.req001",
        "timestamp": "1732345685",
        "type": "text",
        "text": {"body": "Hello"},
    }
    del raw_message[missing_key]

    with pytest.raises(PayloadValidationError):
        extract_single_message(raw_message)


def test_text_missing_body():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.text_err",
        "timestamp": "1732345685",
        "type": "text",
        "text": {},
    }
    with pytest.raises(PayloadValidationError):
        extract_single_message(raw_message)


def test_image_missing_media_id():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.img_err",
        "timestamp": "1732345685",
        "type": "image",
        "image": {"mime_type": "image/jpeg"},
    }
    with pytest.raises(PayloadValidationError):
        extract_single_message(raw_message)


def test_audio_missing_media_id():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.audio_err",
        "timestamp": "1732345685",
        "type": "audio",
        "audio": {"mime_type": "audio/ogg"},
    }
    with pytest.raises(PayloadValidationError):
        extract_single_message(raw_message)


def test_location_missing_coordinates():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.loc_err",
        "timestamp": "1732345685",
        "type": "location",
        "location": {"name": "Central Gate"},
    }
    with pytest.raises(PayloadValidationError):
        extract_single_message(raw_message)


def test_location_invalid_coordinate_format():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.loc_err2",
        "timestamp": "1732345685",
        "type": "location",
        "location": {"latitude": "invalid_latitude", "longitude": 77.2},
    }
    with pytest.raises(PayloadValidationError):
        extract_single_message(raw_message)


# ==========================================
# 7. Unsupported Message Types
# ==========================================


def test_unsupported_message_type_flagged_safely():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.doc001",
        "timestamp": "1732345686",
        "type": "document",
        "document": {"id": "doc123", "filename": "report.pdf"},
    }
    msg = extract_single_message(raw_message, raise_on_unsupported=False)

    assert msg.message_type == NormalizedMessageType.UNSUPPORTED
    assert msg.raw_type == "document"
    assert msg.sender == "919876543210"


def test_unsupported_message_type_raises_when_requested():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.sticker001",
        "timestamp": "1732345687",
        "type": "sticker",
        "sticker": {"id": "stk123"},
    }
    with pytest.raises(UnsupportedMessageTypeError):
        extract_single_message(raw_message, raise_on_unsupported=True)


# ==========================================
# 8. Empty or Malformed Payloads
# ==========================================


@pytest.mark.parametrize(
    "malformed_payload",
    [
        {},
        {"object": "whatsapp_business_account"},
        {"entry": "not_a_list"},
        {"entry": [{"changes": "not_a_list"}]},
        {"entry": [{"changes": [{"value": "not_a_dict"}]}]},
    ],
)
def test_malformed_payloads_raise_validation_error(malformed_payload):
    with pytest.raises(PayloadValidationError):
        parse_webhook_payload(malformed_payload)


# ==========================================
# 9. Multiple Messages in One Webhook
# ==========================================


def test_multiple_messages_in_one_webhook():
    msg1 = {
        "from": "919876543210",
        "id": "wamid.multi001",
        "timestamp": "1732345688",
        "type": "text",
        "text": {"body": "First message"},
    }
    msg2 = {
        "from": "919876543210",
        "id": "wamid.multi002",
        "timestamp": "1732345689",
        "type": "image",
        "image": {"id": "media_img_multi", "mime_type": "image/jpeg"},
    }
    payload = _build_meta_payload(messages=[msg1, msg2])

    parsed = parse_webhook_payload(payload)

    assert len(parsed.messages) == 2
    assert parsed.messages[0].whatsapp_message_id == "wamid.multi001"
    assert parsed.messages[0].message_type == NormalizedMessageType.TEXT
    assert parsed.messages[1].whatsapp_message_id == "wamid.multi002"
    assert parsed.messages[1].message_type == NormalizedMessageType.IMAGE


# ==========================================
# 10. Status Notifications Without Incoming Messages
# ==========================================


def test_status_notifications_separated_from_messages():
    raw_status = {
        "id": "wamid.sent001",
        "status": "delivered",
        "timestamp": "1732345690",
        "recipient_id": "919876543210",
    }
    payload = _build_meta_payload(statuses=[raw_status])

    parsed = parse_webhook_payload(payload)

    # Messages list must be empty
    assert len(parsed.messages) == 0
    # Statuses list must contain the normalized status
    assert len(parsed.statuses) == 1

    status = parsed.statuses[0]
    assert isinstance(status, NormalizedStatus)
    assert status.status == "delivered"
    assert status.whatsapp_message_id == "wamid.sent001"
    assert status.recipient_id == "919876543210"
    assert status.timestamp == "1732345690"

    # Convenience helper extract_messages must also ignore status events
    extracted = extract_messages(payload)
    assert len(extracted) == 0


def test_status_missing_required_fields():
    with pytest.raises(PayloadValidationError):
        extract_single_status({"status": "delivered"})  # missing id and recipient_id


# ==========================================
# 11. extract_messages Filter Convenience
# ==========================================


def test_extract_messages_omits_unsupported_when_requested():
    msg_valid = {
        "from": "919876543210",
        "id": "wamid.valid",
        "timestamp": "1732345691",
        "type": "text",
        "text": {"body": "Valid text"},
    }
    msg_unsupported = {
        "from": "919876543210",
        "id": "wamid.unsupp",
        "timestamp": "1732345692",
        "type": "video",
        "video": {"id": "vid123"},
    }
    payload = _build_meta_payload(messages=[msg_valid, msg_unsupported])

    filtered = extract_messages(payload, include_unsupported=False)
    assert len(filtered) == 1
    assert filtered[0].whatsapp_message_id == "wamid.valid"
