from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.whatsapp_message import (
    IncomingWhatsAppMessage,
    InteractiveReply,
    InteractiveReplyType,
    MessageKind,
)

# Backward-compatibility aliases
NormalizedMessageType = MessageKind
NormalizedMessage = IncomingWhatsAppMessage


class PayloadValidationError(ValueError):
    """Raised when an incoming webhook payload is malformed or missing required fields."""

    pass


class UnsupportedMessageTypeError(ValueError):
    """Raised when an incoming message type is not supported."""

    pass


class NormalizedStatus(BaseModel):
    """Pydantic model representing an outgoing WhatsApp message delivery status."""

    model_config = ConfigDict(frozen=True)

    status: str
    whatsapp_message_id: str
    recipient_id: str
    timestamp: str | None = None
    errors: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert status event into a dictionary."""
        return self.model_dump()


class ParsedWebhookPayload(BaseModel):
    """Pydantic container separating incoming messages from status events."""

    model_config = ConfigDict(frozen=True)

    messages: list[IncomingWhatsAppMessage] = Field(default_factory=list)
    statuses: list[NormalizedStatus] = Field(default_factory=list)


def _parse_timestamp(timestamp_val: Any) -> datetime:
    """Parse a timestamp into a timezone-aware datetime normalized to UTC."""
    if isinstance(timestamp_val, datetime):
        if timestamp_val.tzinfo is None:
            raise PayloadValidationError("Timestamp must be timezone-aware")
        return timestamp_val.astimezone(UTC)

    if isinstance(timestamp_val, (int, float)):
        try:
            return datetime.fromtimestamp(timestamp_val, tz=UTC)
        except (OverflowError, OSError, ValueError) as err:
            raise PayloadValidationError(f"Invalid timestamp value: {err}") from err

    if isinstance(timestamp_val, str):
        # First try numeric unix timestamp (string of digits/float)
        try:
            ts_float = float(timestamp_val)
            return datetime.fromtimestamp(ts_float, tz=UTC)
        except ValueError:
            # Fallback to ISO format string
            try:
                dt = datetime.fromisoformat(timestamp_val)
            except Exception as err:
                raise PayloadValidationError(
                    f"Invalid ISO timestamp format: {timestamp_val}"
                ) from err
            if dt.tzinfo is None:
                raise PayloadValidationError("Timestamp must be timezone-aware")
            return dt.astimezone(UTC)

    raise PayloadValidationError(f"Unsupported timestamp type: {type(timestamp_val)}")


def _extract_text_details(message_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate text message content."""
    text_obj = message_dict.get("text")
    if not isinstance(text_obj, dict):
        raise PayloadValidationError("Text message payload missing 'text' object")

    body = text_obj.get("body")
    if body is None or not str(body).strip():
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


def _extract_interactive_details(message_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate interactive button or list reply."""
    interactive_obj = message_dict.get("interactive")
    if not isinstance(interactive_obj, dict):
        raise PayloadValidationError(
            "Interactive message payload missing 'interactive' object"
        )

    button_reply = interactive_obj.get("button_reply")
    if isinstance(button_reply, dict):
        reply_id = button_reply.get("id")
        title = button_reply.get("title")
        if not reply_id or not title:
            raise PayloadValidationError("Interactive button reply missing id or title")
        return {
            "interactive_reply": InteractiveReply(
                reply_type=InteractiveReplyType.BUTTON,
                reply_id=str(reply_id),
                title=str(title),
            )
        }

    list_reply = interactive_obj.get("list_reply")
    if isinstance(list_reply, dict):
        reply_id = list_reply.get("id")
        title = list_reply.get("title")
        if not reply_id or not title:
            raise PayloadValidationError("Interactive list reply missing id or title")
        return {
            "interactive_reply": InteractiveReply(
                reply_type=InteractiveReplyType.LIST,
                reply_id=str(reply_id),
                title=str(title),
            )
        }

    raise PayloadValidationError("Interactive message missing supported reply content")


def _extract_button_details(message_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate quick reply button click."""
    button_obj = message_dict.get("button")
    if not isinstance(button_obj, dict):
        raise PayloadValidationError("Button message payload missing 'button' object")

    reply_id = button_obj.get("payload") or button_obj.get("text")
    title = button_obj.get("text") or button_obj.get("payload")
    if not reply_id or not title:
        raise PayloadValidationError("Button message missing text or payload")

    return {
        "interactive_reply": InteractiveReply(
            reply_type=InteractiveReplyType.BUTTON,
            reply_id=str(reply_id),
            title=str(title),
        )
    }


def extract_single_message(
    message_dict: dict[str, Any],
    raise_on_unsupported: bool = False,
) -> IncomingWhatsAppMessage:
    """Normalize a single raw WhatsApp message dictionary into an IncomingWhatsAppMessage.

    Args:
        message_dict: Dictionary representing a single item in value["messages"].
        raise_on_unsupported: If True, raise UnsupportedMessageTypeError when
            encountering unsupported message types. Defaults to False.

    Returns:
        IncomingWhatsAppMessage: The validated Pydantic message instance.

    Raises:
        PayloadValidationError: If required metadata or payload fields are missing or invalid.
        UnsupportedMessageTypeError: If the message type is unsupported and
            raise_on_unsupported is True.
    """
    if not isinstance(message_dict, dict):
        raise PayloadValidationError("Message item must be a dictionary")

    sender = message_dict.get("from")
    whatsapp_message_id = message_dict.get("id")
    raw_timestamp = message_dict.get("timestamp")
    raw_type = message_dict.get("type")

    if not sender:
        raise PayloadValidationError("Message missing required 'from' field")
    if not whatsapp_message_id:
        raise PayloadValidationError("Message missing required 'id' field")
    if raw_timestamp is None or raw_timestamp == "":
        raise PayloadValidationError("Message missing required 'timestamp' field")
    if not raw_type:
        raise PayloadValidationError("Message missing required 'type' field")

    whatsapp_number = str(sender)
    whatsapp_message_id = str(whatsapp_message_id)
    timestamp = _parse_timestamp(raw_timestamp)
    raw_type_str = str(raw_type).lower()

    if raw_type_str == "text":
        details = _extract_text_details(message_dict)
        msg_kind = MessageKind.TEXT
    elif raw_type_str == "image":
        details = _extract_image_details(message_dict)
        msg_kind = MessageKind.IMAGE
    elif raw_type_str in ("audio", "voice"):
        details = _extract_audio_details(message_dict)
        msg_kind = MessageKind.AUDIO
    elif raw_type_str == "location":
        details = _extract_location_details(message_dict)
        msg_kind = MessageKind.LOCATION
    elif raw_type_str == "interactive":
        details = _extract_interactive_details(message_dict)
        msg_kind = MessageKind.INTERACTIVE
    elif raw_type_str == "button":
        details = _extract_button_details(message_dict)
        msg_kind = MessageKind.INTERACTIVE
    else:
        if raise_on_unsupported:
            raise UnsupportedMessageTypeError(
                f"Unsupported WhatsApp message type: {raw_type}"
            )
        details = {}
        msg_kind = MessageKind.UNSUPPORTED

    try:
        return IncomingWhatsAppMessage(
            whatsapp_number=whatsapp_number,
            whatsapp_message_id=whatsapp_message_id,
            timestamp=timestamp,
            message_type=msg_kind,
            raw_type=raw_type_str,
            **details,
        )
    except (ValidationError, ValueError) as err:
        raise PayloadValidationError(f"Pydantic validation error: {err}") from err


def extract_single_status(status_dict: dict[str, Any]) -> NormalizedStatus:
    """Normalize a single status notification dictionary from Meta into a Pydantic NormalizedStatus."""
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

    try:
        return NormalizedStatus(
            status=str(status),
            whatsapp_message_id=str(message_id),
            recipient_id=str(recipient_id),
            timestamp=str(timestamp) if timestamp is not None else None,
            errors=errors if isinstance(errors, list) else None,
        )
    except ValidationError as err:
        raise PayloadValidationError(f"Pydantic validation error: {err}") from err


def parse_webhook_payload(
    payload: dict[str, Any],
    raise_on_unsupported: bool = False,
) -> ParsedWebhookPayload:
    """Parse a full Meta WhatsApp webhook payload into normalized messages and statuses.

    Args:
        payload: The parsed JSON body of the webhook event.
        raise_on_unsupported: Whether to raise an exception on unsupported message types.

    Returns:
        ParsedWebhookPayload: Pydantic model holding validated messages and statuses.

    Raises:
        PayloadValidationError: If payload is empty, malformed, or missing required structure.
    """
    if not payload or not isinstance(payload, dict):
        raise PayloadValidationError("Webhook payload must be a non-empty dictionary")

    entries = payload.get("entry")
    if entries is None or not isinstance(entries, list):
        raise PayloadValidationError("Payload missing required 'entry' list")

    messages: list[IncomingWhatsAppMessage] = []
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
) -> list[IncomingWhatsAppMessage]:
    """Convenience function returning only normalized incoming messages.

    Status notifications are automatically filtered out.

    Args:
        payload: Raw Meta webhook payload dictionary.
        raise_on_unsupported: Whether to raise on unsupported message types.
        include_unsupported: If False, omits messages categorized as UNSUPPORTED.

    Returns:
        list[IncomingWhatsAppMessage]: Extracted incoming messages.
    """
    parsed = parse_webhook_payload(
        payload=payload,
        raise_on_unsupported=raise_on_unsupported,
    )

    if include_unsupported:
        return parsed.messages

    return [
        m for m in parsed.messages if m.message_type != MessageKind.UNSUPPORTED
    ]
