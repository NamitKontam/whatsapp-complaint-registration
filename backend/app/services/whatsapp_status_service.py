from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.outbound_whatsapp_message import OutboundWhatsAppMessage
from app.models.pending_whatsapp_status import PendingWhatsAppStatus
from app.services.whatsapp_payload_service import NormalizedStatus

_STATUS_ORDER = {
    "sent": 1,
    "failed": 2,
    "delivered": 3,
    "read": 4,
}


def _parse_provider_timestamp(value: str | None) -> datetime | None:
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


def _get_error_code(errors: list[dict[str, Any]] | None) -> str | None:
    if not errors:
        return None
    code = errors[0].get("code")
    if isinstance(code, (str, int)):
        return str(code)[:100]
    return None


def _apply_provider_status(
    outbound_message: OutboundWhatsAppMessage,
    *,
    status: str,
    provider_timestamp: str | None,
    error_code: str | None,
) -> bool:
    incoming_time = _parse_provider_timestamp(provider_timestamp)
    current_time = outbound_message.provider_status_timestamp
    current_status = outbound_message.provider_status

    if current_status == status and (
        incoming_time is None or incoming_time == current_time
    ):
        return False
    if (
        current_time is not None
        and incoming_time is not None
        and incoming_time < current_time
    ):
        return False
    if (
        current_status is not None
        and _STATUS_ORDER[status] < _STATUS_ORDER[current_status]
    ):
        return False

    outbound_message.provider_status = status
    if incoming_time is not None and (
        current_time is None or incoming_time >= current_time
    ):
        outbound_message.provider_status_timestamp = incoming_time

    if status == "failed":
        if error_code:
            outbound_message.last_error = (
                f"WhatsApp reported delivery failure (error code {error_code})."
            )
        else:
            outbound_message.last_error = "WhatsApp reported delivery failure."
    return True


def _pending_sort_key(event: PendingWhatsAppStatus) -> tuple[datetime, int]:
    timestamp = _parse_provider_timestamp(event.provider_timestamp)
    return (
        timestamp or datetime.min.replace(tzinfo=UTC),
        _STATUS_ORDER[event.status],
    )


def reconcile_pending_whatsapp_statuses(
    db: Session,
    whatsapp_message_id: str,
) -> int:
    """Apply callbacks received before a sent row had its provider ID."""

    outbound_message = db.scalar(
        select(OutboundWhatsAppMessage)
        .where(OutboundWhatsAppMessage.whatsapp_message_id == whatsapp_message_id)
        .with_for_update()
    )
    if outbound_message is None:
        return 0

    events = list(
        db.scalars(
            select(PendingWhatsAppStatus).where(
                PendingWhatsAppStatus.whatsapp_message_id == whatsapp_message_id,
                PendingWhatsAppStatus.recipient_id == outbound_message.recipient,
            )
        )
    )
    if not events:
        return 0

    for event in sorted(events, key=_pending_sort_key):
        _apply_provider_status(
            outbound_message,
            status=event.status,
            provider_timestamp=event.provider_timestamp,
            error_code=event.error_code,
        )
        db.delete(event)

    db.commit()
    return len(events)


def process_whatsapp_status_callback(
    db: Session,
    status_event: NormalizedStatus,
) -> bool:
    """Apply a Meta status callback, staging it if its outbox row is not linked yet."""

    status = status_event.status.lower()
    if status not in _STATUS_ORDER:
        return False

    outbound_message = db.scalar(
        select(OutboundWhatsAppMessage)
        .where(
            OutboundWhatsAppMessage.whatsapp_message_id
            == status_event.whatsapp_message_id
        )
        .with_for_update()
    )
    if outbound_message is not None:
        if outbound_message.recipient != status_event.recipient_id:
            return False
        changed = _apply_provider_status(
            outbound_message,
            status=status,
            provider_timestamp=status_event.timestamp,
            error_code=_get_error_code(status_event.errors),
        )
        if changed:
            db.commit()
        pending_count = reconcile_pending_whatsapp_statuses(
            db,
            status_event.whatsapp_message_id,
        )
        return changed or pending_count > 0

    existing_event = db.scalar(
        select(PendingWhatsAppStatus).where(
            PendingWhatsAppStatus.whatsapp_message_id
            == status_event.whatsapp_message_id,
            PendingWhatsAppStatus.status == status,
        )
    )
    if existing_event is None:
        pending_event = PendingWhatsAppStatus(
            whatsapp_message_id=status_event.whatsapp_message_id,
            recipient_id=status_event.recipient_id,
            status=status,
            provider_timestamp=status_event.timestamp,
            error_code=_get_error_code(status_event.errors),
        )
        db.add(pending_event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()

    # Close the race where the send response was committed while this callback
    # was being staged.
    return bool(
        reconcile_pending_whatsapp_statuses(
            db,
            status_event.whatsapp_message_id,
        )
    )
