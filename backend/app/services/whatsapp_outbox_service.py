import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from math import isfinite

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.services.whatsapp_sender_service import (
    WhatsAppRetryableSendError,
    WhatsAppSendError,
    WhatsAppUncertainSendError,
    send_whatsapp_interactive_message,
    send_whatsapp_text_message,
)


def _normalize_now(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _retry_after_at(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None

    try:
        seconds = float(value)
        if not isfinite(seconds):
            return None
        if seconds <= 0:
            return now
        try:
            return now + timedelta(seconds=seconds)
        except OverflowError:
            return datetime.max.replace(tzinfo=UTC)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    try:
        retry_at = retry_at.astimezone(UTC)
    except OverflowError:
        return datetime.max.replace(tzinfo=UTC)
    return max(now, retry_at)


def _add_seconds_safely(value: datetime, seconds: float) -> datetime:
    try:
        return value + timedelta(seconds=seconds)
    except OverflowError:
        return datetime.max.replace(tzinfo=UTC)


def _next_retry_at(
    attempts: int,
    *,
    base_seconds: int,
    max_seconds: int,
    retry_after: str | None,
    now: datetime,
) -> datetime:
    delay = min(base_seconds, max_seconds)
    for _ in range(max(0, attempts - 1)):
        delay = min(delay * 2, max_seconds)

    backoff_at = _add_seconds_safely(now, delay)
    retry_after_at = _retry_after_at(retry_after, now)
    maximum_at = _add_seconds_safely(now, max_seconds)
    return min(max(backoff_at, retry_after_at or backoff_at), maximum_at)


def _set_safe_retry(
    outbound_message: OutboundWhatsAppMessage,
    error: WhatsAppRetryableSendError,
    *,
    now: datetime,
) -> None:
    settings = get_settings()
    outbound_message.last_error = str(error)

    if outbound_message.attempts >= settings.whatsapp_outbox_max_attempts:
        outbound_message.status = OutboundMessageStatus.FAILED
        outbound_message.next_attempt_at = None
        return

    outbound_message.next_attempt_at = _next_retry_at(
        outbound_message.attempts,
        base_seconds=settings.whatsapp_outbox_retry_base_seconds,
        max_seconds=settings.whatsapp_outbox_retry_max_seconds,
        retry_after=error.retry_after,
        now=now,
    )
    outbound_message.status = OutboundMessageStatus.RETRY_SCHEDULED


async def dispatch_outbound_message(
    db: Session,
    outbound_message_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Dispatch an eligible outbox row without retrying uncertain deliveries."""

    processing_time = _normalize_now(now)
    outbound_message = db.scalar(
        select(OutboundWhatsAppMessage)
        .where(OutboundWhatsAppMessage.id == outbound_message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if outbound_message is None:
        return False

    if outbound_message.status == OutboundMessageStatus.SENT:
        return True

    if outbound_message.status not in {
        OutboundMessageStatus.PENDING,
        OutboundMessageStatus.RETRY_SCHEDULED,
    }:
        return False

    if (
        outbound_message.status == OutboundMessageStatus.PENDING
        and outbound_message.attempts > 0
    ):
        outbound_message.status = OutboundMessageStatus.UNCERTAIN
        outbound_message.next_attempt_at = None
        outbound_message.last_error = (
            "A legacy send attempt has no recorded outcome; reconciliation is required."
        )
        db.commit()
        return False

    if outbound_message.status == OutboundMessageStatus.RETRY_SCHEDULED and (
        outbound_message.next_attempt_at is None
        or outbound_message.next_attempt_at > processing_time
    ):
        return False

    settings = get_settings()
    if outbound_message.attempts >= settings.whatsapp_outbox_max_attempts:
        outbound_message.status = OutboundMessageStatus.FAILED
        outbound_message.next_attempt_at = None
        outbound_message.last_error = (
            outbound_message.last_error or "Maximum send attempts reached."
        )
        db.commit()
        return False

    # Persist uncertainty before the external call. A process crash at any
    # point after this commit must not cause an automatic duplicate send.
    outbound_message.attempts += 1
    outbound_message.status = OutboundMessageStatus.UNCERTAIN
    outbound_message.last_attempt_at = processing_time
    outbound_message.next_attempt_at = None
    outbound_message.last_error = (
        "Send attempt started; provider acceptance is not yet confirmed."
    )
    db.commit()

    try:
        if outbound_message.interactive_buttons is None:
            response = await send_whatsapp_text_message(
                recipient=outbound_message.recipient,
                text=outbound_message.body,
            )
        else:
            response = await send_whatsapp_interactive_message(
                recipient=outbound_message.recipient,
                text=outbound_message.body,
                buttons=outbound_message.interactive_buttons,
            )

        if not isinstance(response, dict):
            raise WhatsAppUncertainSendError(
                "WhatsApp returned an invalid success response."
            )

        messages = response.get("messages")
        first_message = messages[0] if isinstance(messages, list) and messages else None
        whatsapp_message_id = (
            first_message.get("id") if isinstance(first_message, dict) else None
        )
        if not isinstance(whatsapp_message_id, str) or not whatsapp_message_id.strip():
            raise WhatsAppUncertainSendError(
                "WhatsApp returned no provider message ID; acceptance is uncertain."
            )

    except WhatsAppRetryableSendError as exc:
        _set_safe_retry(outbound_message, exc, now=processing_time)
        db.commit()
        return False

    except WhatsAppUncertainSendError as exc:
        outbound_message.status = OutboundMessageStatus.UNCERTAIN
        outbound_message.next_attempt_at = None
        outbound_message.last_error = str(exc)
        db.commit()
        return False

    except WhatsAppSendError as exc:
        outbound_message.status = OutboundMessageStatus.FAILED
        outbound_message.next_attempt_at = None
        outbound_message.last_error = str(exc)
        db.commit()
        return False

    outbound_message.status = OutboundMessageStatus.SENT
    outbound_message.whatsapp_message_id = whatsapp_message_id.strip()
    outbound_message.sent_at = processing_time
    outbound_message.next_attempt_at = None
    outbound_message.last_error = None
    db.commit()

    from app.services.whatsapp_status_service import (
        reconcile_pending_whatsapp_statuses,
    )

    reconcile_pending_whatsapp_statuses(db, outbound_message.whatsapp_message_id)
    return True


async def dispatch_due_outbound_messages(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> int:
    """Dispatch a bounded batch of due outbox messages."""

    processing_time = _normalize_now(now)
    due_filter = or_(
        and_(
            OutboundWhatsAppMessage.status == OutboundMessageStatus.PENDING,
            OutboundWhatsAppMessage.next_attempt_at.is_(None),
        ),
        and_(
            OutboundWhatsAppMessage.status == OutboundMessageStatus.RETRY_SCHEDULED,
            OutboundWhatsAppMessage.next_attempt_at <= processing_time,
        ),
    )
    message_ids = list(
        db.scalars(
            select(OutboundWhatsAppMessage.id)
            .where(due_filter)
            .order_by(OutboundWhatsAppMessage.created_at)
            .limit(limit)
        )
    )

    for message_id in message_ids:
        await dispatch_outbound_message(
            db,
            message_id,
            now=processing_time,
        )

    return len(message_ids)
