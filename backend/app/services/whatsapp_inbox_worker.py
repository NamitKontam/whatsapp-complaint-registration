import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.inbound_whatsapp_event import (
    InboundEventStatus,
    InboundEventType,
    InboundWhatsAppEvent,
)
from app.schemas.whatsapp_message import IncomingWhatsAppMessage
from app.services.complaint_processing_service import process_incoming_message
from app.services.whatsapp_payload_service import NormalizedStatus
from app.services.whatsapp_status_service import process_whatsapp_status_callback

logger = logging.getLogger(__name__)


def _retry_delay_seconds(attempt_count: int) -> int:
    settings = get_settings()
    delay = settings.whatsapp_inbox_retry_base_seconds * 2 ** max(
        attempt_count - 1,
        0,
    )
    return min(delay, settings.whatsapp_inbox_retry_max_seconds)


def claim_next_inbound_event(
    db: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> uuid.UUID | None:
    settings = get_settings()
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    current_time = current_time.astimezone(UTC)

    expired_at_limit = (
        update(InboundWhatsAppEvent)
        .where(
            InboundWhatsAppEvent.status == InboundEventStatus.PROCESSING,
            InboundWhatsAppEvent.lease_expires_at <= current_time,
            InboundWhatsAppEvent.attempt_count >= settings.whatsapp_inbox_max_attempts,
        )
        .values(
            status=InboundEventStatus.FAILED,
            lease_expires_at=None,
            claimed_by=None,
            last_error="Worker lease expired after maximum attempts",
            updated_at=current_time,
        )
    )
    db.execute(expired_at_limit)

    available = or_(
        and_(
            InboundWhatsAppEvent.status.in_(
                (
                    InboundEventStatus.PENDING,
                    InboundEventStatus.RETRY_SCHEDULED,
                )
            ),
            InboundWhatsAppEvent.available_at <= current_time,
        ),
        and_(
            InboundWhatsAppEvent.status == InboundEventStatus.PROCESSING,
            InboundWhatsAppEvent.lease_expires_at <= current_time,
            InboundWhatsAppEvent.attempt_count < settings.whatsapp_inbox_max_attempts,
        ),
    )
    event = db.scalar(
        select(InboundWhatsAppEvent)
        .where(available)
        .order_by(
            InboundWhatsAppEvent.available_at,
            InboundWhatsAppEvent.created_at,
            InboundWhatsAppEvent.id,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if event is None:
        db.commit()
        return None

    event.status = InboundEventStatus.PROCESSING
    event.attempt_count += 1
    event.claimed_by = worker_id
    event.lease_expires_at = current_time + timedelta(
        seconds=settings.whatsapp_inbox_lease_seconds,
    )
    event.updated_at = current_time
    db.commit()
    return event.id


def _record_failure(
    db: Session,
    *,
    event_id: uuid.UUID,
    worker_id: str,
    attempt_count: int,
    error: Exception,
    now: datetime,
) -> None:
    settings = get_settings()
    terminal = attempt_count >= settings.whatsapp_inbox_max_attempts
    available_at = (
        now
        if terminal
        else now + timedelta(seconds=_retry_delay_seconds(attempt_count))
    )
    db.execute(
        update(InboundWhatsAppEvent)
        .where(
            InboundWhatsAppEvent.id == event_id,
            InboundWhatsAppEvent.status == InboundEventStatus.PROCESSING,
            InboundWhatsAppEvent.claimed_by == worker_id,
        )
        .values(
            status=(
                InboundEventStatus.FAILED
                if terminal
                else InboundEventStatus.RETRY_SCHEDULED
            ),
            available_at=available_at,
            lease_expires_at=None,
            claimed_by=None,
            last_error=type(error).__name__,
            updated_at=now,
        )
    )
    db.commit()


async def process_one_inbound_event(
    db: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    current_time = current_time.astimezone(UTC)

    event_id = claim_next_inbound_event(
        db,
        worker_id=worker_id,
        now=current_time,
    )
    if event_id is None:
        return False

    event = db.get(InboundWhatsAppEvent, event_id)
    if event is None:
        raise RuntimeError("Claimed inbound event disappeared")
    event_type = event.event_type
    event_payload = event.event_payload
    attempt_count = event.attempt_count

    try:
        if event_type == InboundEventType.MESSAGE:
            message = IncomingWhatsAppMessage.model_validate(event_payload)
            await process_incoming_message(db=db, message=message)
        elif event_type == InboundEventType.STATUS_CALLBACK:
            status_event = NormalizedStatus.model_validate(event_payload)
            process_whatsapp_status_callback(db=db, status_event=status_event)
        else:
            raise ValueError("Unknown inbound event type")
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        db.rollback()
        _record_failure(
            db,
            event_id=event_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            error=error,
            now=current_time,
        )
        logger.warning(
            "Inbound event processing failed: event_id=%s event_type=%s error_type=%s",
            event_id,
            event_type.value,
            type(error).__name__,
        )
        return True

    db.execute(
        update(InboundWhatsAppEvent)
        .where(
            InboundWhatsAppEvent.id == event_id,
            InboundWhatsAppEvent.status == InboundEventStatus.PROCESSING,
            InboundWhatsAppEvent.claimed_by == worker_id,
        )
        .values(
            status=InboundEventStatus.COMPLETED,
            processed_at=current_time,
            lease_expires_at=None,
            claimed_by=None,
            last_error=None,
            updated_at=current_time,
        )
    )
    db.commit()
    return True


async def run_inbound_inbox_worker() -> None:
    """Continuously claim durable webhook events and process them."""
    settings = get_settings()
    worker_id = str(uuid.uuid4())
    while True:
        db = SessionLocal()
        try:
            processed = await process_one_inbound_event(
                db,
                worker_id=worker_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            db.rollback()
            logger.error(
                "Inbound inbox poll failed: error_type=%s",
                type(error).__name__,
            )
            processed = False
        finally:
            db.close()

        if not processed:
            await asyncio.sleep(settings.whatsapp_inbox_poll_interval_seconds)
