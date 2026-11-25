from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import app
from app.schemas.whatsapp_message import (
    IncomingWhatsAppMessage,
    InteractiveReply,
    InteractiveReplyType,
    MessageKind,
)
from app.services.whatsapp_payload_service import (
    NormalizedStatus,
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
    assert isinstance(msg, IncomingWhatsAppMessage)
    assert isinstance(msg, BaseModel)
    assert msg.whatsapp_number == "919876543210"
    assert msg.whatsapp_message_id == "wamid.text001"
    assert msg.message_type == MessageKind.TEXT
    assert msg.text_content == "There is water leakage near my house."
    assert msg.timestamp == datetime.fromtimestamp(1732345678, tz=UTC)
    assert msg.timestamp.tzinfo == UTC
    assert msg.media_id is None
    assert msg.latitude is None
    assert msg.longitude is None
    assert msg.interactive_reply is None

    # Check Pydantic model_dump() and to_dict() contract
    dumped = msg.model_dump()
    assert dumped["whatsapp_number"] == "919876543210"
    msg_dict = msg.to_dict()
    assert msg_dict["message_type"] == "TEXT"
    assert msg_dict["text_content"] == "There is water leakage near my house."


# ==========================================
# 2. Valid Image Message Tests (With Caption)
# ==========================================


def test_parse_valid_image_with_caption():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.img001",
        "timestamp": "1732345679",
        "type": "image",
        "image": {
            "id": "media_img_12345",
            "mime_type": "image/jpeg",
            "caption": "Pothole photo for evidence",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.IMAGE
    assert msg.media_id == "media_img_12345"
    assert msg.media_mime_type == "image/jpeg"
    assert msg.caption == "Pothole photo for evidence"
    assert msg.text_content is None


# ==========================================
# 3. Valid Image Message Tests (Without Caption)
# ==========================================


def test_parse_valid_image_without_caption():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.img002",
        "timestamp": "1732345680",
        "type": "image",
        "image": {
            "id": "media_img_67890",
            "mime_type": "image/png",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.IMAGE
    assert msg.media_id == "media_img_67890"
    assert msg.media_mime_type == "image/png"
    assert msg.caption is None
    assert msg.text_content is None


# ==========================================
# 4. Valid Audio & Voice Message Tests
# ==========================================


def test_parse_valid_audio_message():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.audio001",
        "timestamp": "1732345681",
        "type": "audio",
        "audio": {
            "id": "media_audio_999",
            "mime_type": "audio/ogg; codecs=opus",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.AUDIO
    assert msg.media_id == "media_audio_999"
    assert msg.media_mime_type == "audio/ogg; codecs=opus"


def test_parse_valid_voice_message():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.voice001",
        "timestamp": "1732345682",
        "type": "voice",
        "voice": {
            "id": "media_voice_888",
            "mime_type": "audio/ogg",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.AUDIO
    assert msg.media_id == "media_voice_888"


# ==========================================
# 5. Location Message Tests
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

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.LOCATION
    assert msg.latitude == Decimal("12.9716")
    assert msg.longitude == Decimal("77.5946")
    assert msg.location_name == "Town Hall"
    assert msg.location_address == "MG Road, Bengaluru"
    assert msg.text_content is None


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

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.latitude == Decimal("28.6139")
    assert msg.longitude == Decimal("77.2090")
    assert msg.location_name is None
    assert msg.location_address is None


# ==========================================
# 6. Interactive Button & List Reply Tests
# ==========================================


def test_parse_valid_interactive_button_reply():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.btn001",
        "timestamp": "1732345684",
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {
                "id": "btn_yes",
                "title": "Yes",
            },
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.INTERACTIVE
    assert msg.raw_type == "interactive"
    assert isinstance(msg.interactive_reply, InteractiveReply)
    assert msg.interactive_reply.reply_type == InteractiveReplyType.BUTTON
    assert msg.interactive_reply.reply_id == "btn_yes"
    assert msg.interactive_reply.title == "Yes"


def test_parse_valid_interactive_list_reply():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.list001",
        "timestamp": "1732345685",
        "type": "interactive",
        "interactive": {
            "type": "list_reply",
            "list_reply": {
                "id": "option_water",
                "title": "Water Leakage",
            },
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.INTERACTIVE
    assert isinstance(msg.interactive_reply, InteractiveReply)
    assert msg.interactive_reply.reply_type == InteractiveReplyType.LIST
    assert msg.interactive_reply.reply_id == "option_water"
    assert msg.interactive_reply.title == "Water Leakage"


def test_parse_valid_quick_reply_button():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.qr001",
        "timestamp": "1732345686",
        "type": "button",
        "button": {
            "text": "No",
            "payload": "no",
        },
    }
    payload = _build_meta_payload(messages=[raw_message])

    parsed = parse_webhook_payload(payload)
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.INTERACTIVE
    assert isinstance(msg.interactive_reply, InteractiveReply)
    assert msg.interactive_reply.reply_type == InteractiveReplyType.BUTTON
    assert msg.interactive_reply.reply_id == "no"
    assert msg.interactive_reply.title == "No"


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

    assert isinstance(msg, IncomingWhatsAppMessage)
    assert msg.message_type == MessageKind.UNSUPPORTED
    assert msg.raw_type == "document"
    assert msg.whatsapp_number == "919876543210"


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
# 8. Multiple Messages in One Webhook
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
    assert parsed.messages[0].message_type == MessageKind.TEXT
    assert parsed.messages[1].whatsapp_message_id == "wamid.multi002"
    assert parsed.messages[1].message_type == MessageKind.IMAGE


# ==========================================
# 9. Missing or Malformed Fields
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
# 10. Timestamp Normalization to UTC
# ==========================================


def test_timestamp_numeric_string_normalized_to_utc():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.ts001",
        "timestamp": "1732345678",
        "type": "text",
        "text": {"body": "Timestamp test"},
    }
    msg = extract_single_message(raw_message)
    assert msg.timestamp.tzinfo == UTC
    assert msg.timestamp == datetime(2024, 11, 23, 7, 7, 58, tzinfo=UTC)


def test_timestamp_iso_with_timezone_offset_normalized_to_utc():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.ts002",
        "timestamp": "2026-11-24T12:00:00+05:30",
        "type": "text",
        "text": {"body": "ISO timestamp test"},
    }
    msg = extract_single_message(raw_message)
    assert msg.timestamp.tzinfo == UTC
    assert msg.timestamp == datetime(2026, 11, 24, 6, 30, 0, tzinfo=UTC)


def test_naive_timestamp_string_rejected():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.ts003",
        "timestamp": "2026-11-24T12:00:00",
        "type": "text",
        "text": {"body": "Naive timestamp"},
    }
    with pytest.raises(PayloadValidationError) as exc_info:
        extract_single_message(raw_message)
    assert "Timestamp must be timezone-aware" in str(exc_info.value)


# ==========================================
# 11. Invalid Coordinates Rejected by Pydantic
# ==========================================


@pytest.mark.parametrize(
    "lat,lon,expected_error",
    [
        (Decimal("95.0"), Decimal("77.0"), "Latitude must be between -90 and 90"),
        (Decimal("-95.0"), Decimal("77.0"), "Latitude must be between -90 and 90"),
        (Decimal("12.0"), Decimal("185.0"), "Longitude must be between -180 and 180"),
        (Decimal("12.0"), Decimal("-185.0"), "Longitude must be between -180 and 180"),
    ],
)
def test_invalid_coordinates_rejected_by_pydantic_validator(lat, lon, expected_error):
    raw_message = {
        "from": "919876543210",
        "id": "wamid.coord_err",
        "timestamp": "1732345685",
        "type": "location",
        "location": {
            "latitude": lat,
            "longitude": lon,
        },
    }
    with pytest.raises(PayloadValidationError) as exc_info:
        extract_single_message(raw_message)
    assert expected_error in str(exc_info.value)


# ==========================================
# 12. Webhook Status Events & Payloads Without Messages
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


def test_payload_with_empty_entry_list():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [],
    }
    parsed = parse_webhook_payload(payload)
    assert parsed.messages == []
    assert parsed.statuses == []


def test_payload_with_entry_having_no_messages_or_statuses():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100200300",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                        },
                    }
                ],
            }
        ],
    }
    parsed = parse_webhook_payload(payload)
    assert parsed.messages == []
    assert parsed.statuses == []


def test_status_missing_required_fields():
    with pytest.raises(PayloadValidationError):
        extract_single_status({"status": "delivered"})  # missing id and recipient_id


# ==========================================
# 13. extract_messages Filter Convenience
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
    assert isinstance(filtered[0], IncomingWhatsAppMessage)


# ==========================================
# 14. Webhook Route Integration Test
# ==========================================



def test_test_parser_endpoint_integration():
    raw_message = {
        "from": "919876543210",
        "id": "wamid.api001",
        "timestamp": "1732345678",
        "type": "text",
        "text": {"body": "Testing endpoint"},
    }

    payload = _build_meta_payload(messages=[raw_message])

    client = TestClient(app)
    response = client.post(
        "/api/webhooks/whatsapp/test-parser",
        json=payload,
    )

    assert response.status_code == 200

    data = response.json()
    assert len(data["messages"]) == 1
    assert data["messages"][0]["whatsapp_number"] == "919876543210"
    assert data["messages"][0]["message_type"] == "TEXT"
    assert data["messages"][0]["text_content"] == "Testing endpoint"