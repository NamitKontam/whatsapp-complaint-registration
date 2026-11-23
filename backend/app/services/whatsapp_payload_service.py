from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


class NormalizedMessageType(str, Enum):
    """Supported normalized incoming WhatsApp message types."""

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    LOCATION = "LOCATION"
    UNSUPPORTED = "UNSUPPORTED"


class PayloadValidationError(ValueError):
    """Raised when an incoming webhook payload is malformed or missing required fields."""

    pass


class UnsupportedMessageTypeError(ValueError):
    """Raised when an incoming message type is not supported."""

    pass


@dataclass(frozen=True)
class NormalizedMessage:
    """Strongly typed normalized representation of an incoming WhatsApp message."""

    sender: str
    whatsapp_message_id: str
    timestamp: str
    message_type: NormalizedMessageType
    text_content: str | None = None
    media_id: str | None = None
    media_mime_type: str | None = None
    caption: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    location_name: str | None = None
    location_address: str | None = None
    raw_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the normalized message into a clean dictionary."""
        data = asdict(self)
        data["message_type"] = self.message_type.value
        return data


@dataclass(frozen=True)
class NormalizedStatus:
    """Strongly typed representation of an outgoing WhatsApp message delivery status."""

    status: str
    whatsapp_message_id: str
    recipient_id: str
    timestamp: str | None = None
    errors: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert status event into a dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class ParsedWebhookPayload:
    """Complete container separating incoming messages from status events."""

    messages: list[NormalizedMessage]
    statuses: list[NormalizedStatus]


def _extract_text_details(message_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate text message content."""
    text_obj = message_dict.get("text")
    if not isinstance(text_obj, dict):
        raise PayloadValidationError("Text message payload missing 'text' object")

    body = text_obj.get("body")
    if body is None:
        raise PayloadValidationError("Text message payload missing 'body'")

    return {"text_content": str(body)}


def _extract_image_details(message_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate image media details."""
    image_obj = message_dict.get("image")
    if not isinstance(image_obj, dict):
        raise PayloadValidationError("Image message payload missing 'image' object")

    media_id = image_obj.get("id")
    if not media_id:
        raise PayloadValidationError("Image message missing media 'id'")

    return {
        "media_id": str(media_id),
        "media_mime_type": image_obj.get("mime_type"),
        "caption": image_obj.get("caption"),
    }


def _extract_audio_details(message_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate audio / voice media details."""
    audio_obj = message_dict.get("audio") or message_dict.get("voice")
    if not isinstance(audio_obj, dict):
        raise PayloadValidationError("Audio message payload missing 'audio' object")

    media_id = audio_obj.get("id")
    if not media_id:
        raise PayloadValidationError("Audio message missing media 'id'")

    return {
        "media_id": str(media_id),
        "media_mime_type": audio_obj.get("mime_type"),
    }


def _extract_location_details(message_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate geographic location details."""
    loc_obj = message_dict.get("location")
    if not isinstance(loc_obj, dict):
        raise PayloadValidationError(
            "Location message payload missing 'location' object"
        )

    lat_val = loc_obj.get("latitude")
    long_val = loc_obj.get("longitude")

    if lat_val is None or long_val is None:
        raise PayloadValidationError(
            "Location message missing required 'latitude' or 'longitude'"
        )

    try:
        latitude = Decimal(str(lat_val))
        longitude = Decimal(str(long_val))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise PayloadValidationError(
            f"Invalid numeric coordinate in location: {err}"
        ) from err

    return {
        "latitude": latitude,
        "longitude": longitude,
        "location_name": loc_obj.get("name"),
        "location_address": loc_obj.get("address"),
    }


def extract_single_message(
    message_dict: dict[str, Any],
    raise_on_unsupported: bool = False,
) -> NormalizedMessage:
    """Normalize a single raw WhatsApp message dictionary.

    Args:
        message_dict: Dictionary representing a single item in value["messages"].
        raise_on_unsupported: If True, raise UnsupportedMessageTypeError when
            encountering unsupported message types. Defaults to False.

    Returns:
        NormalizedMessage: The normalized message representation.

    Raises:
        PayloadValidationError: If required metadata or payload fields are missing.
        UnsupportedMessageTypeError: If the message type is unsupported and
            raise_on_unsupported is True.
    """
    if not isinstance(message_dict, dict):
        raise PayloadValidationError("Message item must be a dictionary")

    sender = message_dict.get("from")
    whatsapp_message_id = message_dict.get("id")
    timestamp = message_dict.get("timestamp")
    raw_type = message_dict.get("type")

    if not sender:
        raise PayloadValidationError("Message missing required 'from' field")
    if not whatsapp_message_id:
        raise PayloadValidationError("Message missing required 'id' field")
    if not timestamp:
        raise PayloadValidationError("Message missing required 'timestamp' field")
    if not raw_type:
        raise PayloadValidationError("Message missing required 'type' field")

    sender = str(sender)
    whatsapp_message_id = str(whatsapp_message_id)
    timestamp = str(timestamp)
    raw_type_str = str(raw_type).lower()

    if raw_type_str == "text":
        details = _extract_text_details(message_dict)
        msg_type = NormalizedMessageType.TEXT
    elif raw_type_str == "image":
        details = _extract_image_details(message_dict)
        msg_type = NormalizedMessageType.IMAGE
    elif raw_type_str in ("audio", "voice"):
        details = _extract_audio_details(message_dict)
        msg_type = NormalizedMessageType.AUDIO
    elif raw_type_str == "location":
        details = _extract_location_details(message_dict)
        msg_type = NormalizedMessageType.LOCATION
    else:
        if raise_on_unsupported:
            raise UnsupportedMessageTypeError(
                f"Unsupported WhatsApp message type: {raw_type}"
            )
        details = {}
        msg_type = NormalizedMessageType.UNSUPPORTED

    return NormalizedMessage(
        sender=sender,
        whatsapp_message_id=whatsapp_message_id,
        timestamp=timestamp,
        message_type=msg_type,
        raw_type=raw_type_str,
        **details,
    )


def extract_single_status(status_dict: dict[str, Any]) -> NormalizedStatus:
    """Normalize a single status notification dictionary from Meta."""
    if not isinstance(status_dict, dict):
        raise PayloadValidationError("Status item must be a dictionary")

    status = status_dict.get("status")
    message_id = status_dict.get("id")
    recipient_id = status_dict.get("recipient_id")
    timestamp = status_dict.get("timestamp")
    errors = status_dict.get("errors")

    if not status or not message_id or not recipient_id:
        raise PayloadValidationError(
            "Status event missing required fields ('status', 'id', or 'recipient_id')"
        )

    return NormalizedStatus(
        status=str(status),
        whatsapp_message_id=str(message_id),
        recipient_id=str(recipient_id),
        timestamp=str(timestamp) if timestamp is not None else None,
        errors=errors if isinstance(errors, list) else None,
    )


def parse_webhook_payload(
    payload: dict[str, Any],
    raise_on_unsupported: bool = False,
) -> ParsedWebhookPayload:
    """Parse a full Meta WhatsApp webhook payload into normalized messages and statuses.

    Args:
        payload: The parsed JSON body of the webhook event.
        raise_on_unsupported: Whether to raise an exception on unsupported message types.

    Returns:
        ParsedWebhookPayload: Container holding normalized messages and statuses.

    Raises:
        PayloadValidationError: If payload is empty, malformed, or missing required structure.
    """
    if not payload or not isinstance(payload, dict):
        raise PayloadValidationError("Webhook payload must be a non-empty dictionary")

    entries = payload.get("entry")
    if entries is None or not isinstance(entries, list):
        raise PayloadValidationError("Payload missing required 'entry' list")

    messages: list[NormalizedMessage] = []
    statuses: list[NormalizedStatus] = []

    for entry in entries:
        if not isinstance(entry, dict):
            raise PayloadValidationError("Malformed payload: entry must be a dictionary")

        changes = entry.get("changes")
        if changes is None or not isinstance(changes, list):
            raise PayloadValidationError("Malformed payload: entry must have 'changes' list")

        for change in changes:
            if not isinstance(change, dict):
                raise PayloadValidationError(
                    "Malformed payload: change must be a dictionary"
                )

            value = change.get("value")
            if not isinstance(value, dict):
                raise PayloadValidationError(
                    "Malformed payload: change value must be a dictionary"
                )

            # Process inbound messages
            raw_messages = value.get("messages", [])
            if isinstance(raw_messages, list):
                for raw_msg in raw_messages:
                    normalized_msg = extract_single_message(
                        raw_msg,
                        raise_on_unsupported=raise_on_unsupported,
                    )
                    messages.append(normalized_msg)

            # Process status receipts (delivered, read, failed, etc.)
            raw_statuses = value.get("statuses", [])
            if isinstance(raw_statuses, list):
                for raw_status in raw_statuses:
                    normalized_status = extract_single_status(raw_status)
                    statuses.append(normalized_status)

    return ParsedWebhookPayload(messages=messages, statuses=statuses)


def extract_messages(
    payload: dict[str, Any],
    raise_on_unsupported: bool = False,
    include_unsupported: bool = True,
) -> list[NormalizedMessage]:
    """Convenience function returning only normalized incoming messages.

    Status notifications are automatically filtered out.

    Args:
        payload: Raw Meta webhook payload dictionary.
        raise_on_unsupported: Whether to raise on unsupported message types.
        include_unsupported: If False, omits messages categorized as UNSUPPORTED.

    Returns:
        list[NormalizedMessage]: Extracted incoming messages.
    """
    parsed = parse_webhook_payload(
        payload=payload,
        raise_on_unsupported=raise_on_unsupported,
    )

    if include_unsupported:
        return parsed.messages

    return [
        m for m in parsed.messages if m.message_type != NormalizedMessageType.UNSUPPORTED
    ]
