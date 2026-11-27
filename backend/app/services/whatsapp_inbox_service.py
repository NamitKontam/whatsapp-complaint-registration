import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.inbound_whatsapp_event import (
    InboundEventStatus,
    InboundEventType,
    InboundWhatsAppEvent,
)
from app.services.whatsapp_payload_service import ParsedWebhookPayload


class InboundEventLeaseLostError(RuntimeError):
    """Raised when a worker no longer owns its inbox event."""


def lock_inbound_event_claim(
    db: Session,
    *,
    event_id: uuid.UUID,
    worker_id: str,
) -> None:
    event = db.scalar(
        select(InboundWhatsAppEvent)
        .where(
            InboundWhatsAppEvent.id == event_id,
            InboundWhatsAppEvent.status == InboundEventStatus.PROCESSING,
            InboundWhatsAppEvent.claimed_by == worker_id,
            InboundWhatsAppEvent.lease_expires_at > func.now(),
        )
        .with_for_update()
    )
    if event is None:
        raise InboundEventLeaseLostError("Inbox event lease is no longer owned")


def _status_provider_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)


def _event_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_rows(parsed_payload: ParsedWebhookPayload) -> list[dict[str, object]]:
    rows = []
    for message in parsed_payload.messages:
        rows.append(
            {
                "id": uuid.uuid4(),
                "event_key": _event_key(f"message:{message.whatsapp_message_id}"),
                "event_type": InboundEventType.MESSAGE,
                "status": InboundEventStatus.PENDING,
                "whatsapp_message_id": message.whatsapp_message_id,
                "sender_identifier": message.whatsapp_number,
                "provider_timestamp": message.timestamp,
                "event_payload": message.model_dump(mode="json"),
                "attempt_count": 0,
            }
        )

    for status in parsed_payload.statuses:
        # The callback processor only consumes the provider error code.
        errors = status.errors or []
        error_code = errors[0].get("code") if errors else None
        safe_errors = (
            [{"code": error_code}] if isinstance(error_code, (int, str)) else None
        )
        payload = status.model_dump(mode="json")
        payload["errors"] = safe_errors
        status_identity = json.dumps(
            [
                status.whatsapp_message_id,
                status.recipient_id,
                status.status.lower(),
                status.timestamp,
                safe_errors,
            ],
            separators=(",", ":"),
        )
        rows.append(
            {
                "id": uuid.uuid4(),
                "event_key": _event_key(f"status:{status_identity}"),
                "event_type": InboundEventType.STATUS_CALLBACK,
                "status": InboundEventStatus.PENDING,
                "whatsapp_message_id": status.whatsapp_message_id,
                "sender_identifier": None,
                "provider_timestamp": _status_provider_timestamp(status.timestamp),
                "event_payload": payload,
                "attempt_count": 0,
            }
        )
    return rows


def persist_webhook_events(
    db: Session,
    parsed_payload: ParsedWebhookPayload,
) -> int:
    """Persist normalized webhook events atomically and ignore duplicate deliveries."""
    rows = _event_rows(parsed_payload)
    if not rows:
        return 0

    statement = (
        insert(InboundWhatsAppEvent)
        .values(rows)
        .on_conflict_do_nothing()
        .returning(InboundWhatsAppEvent.id)
    )
    try:
        inserted_ids = db.scalars(statement).all()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(inserted_ids)
