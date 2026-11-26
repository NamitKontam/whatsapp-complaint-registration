import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.inbound_whatsapp_event import (
    InboundEventStatus,
    InboundEventType,
    InboundWhatsAppEvent,
)
from app.schemas.whatsapp_message import IncomingWhatsAppMessage
from app.services.complaint_processing_service import process_incoming_message
from app.services.whatsapp_inbox_service import (
    InboundEventLeaseLostError,
    lock_inbound_event_claim,
)
from app.services.whatsapp_media_service import WhatsAppMediaDownloadError
from app.services.whatsapp_payload_service import NormalizedStatus
from app.services.whatsapp_status_service import process_whatsapp_status_callback

logger = logging.getLogger(__name__)


def _current_utc_time(db: Session, value: datetime | None = None) -> datetime:
    current_time = value if value is not None else db.scalar(select(func.now()))
    if current_time is None:
        raise RuntimeError("Unable to read the database clock")
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return current_time.astimezone(UTC)


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
    """Claim an event, ordering queued messages per sender by provider time.

    Late arrivals are processed when eligible but do not replay completed events.
    """
    settings = get_settings()
    current_time = _current_utc_time(db, now)

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

    queued_statuses = (
        InboundEventStatus.PENDING,
        InboundEventStatus.PROCESSING,
        InboundEventStatus.RETRY_SCHEDULED,
    )
    earlier = aliased(InboundWhatsAppEvent)
    earlier_than_candidate = or_(
        earlier.provider_timestamp < InboundWhatsAppEvent.provider_timestamp,
        and_(
            earlier.provider_timestamp == InboundWhatsAppEvent.provider_timestamp,
            earlier.created_at < InboundWhatsAppEvent.created_at,
        ),
        and_(
            earlier.provider_timestamp == InboundWhatsAppEvent.provider_timestamp,
            earlier.created_at == InboundWhatsAppEvent.created_at,
            earlier.id < InboundWhatsAppEvent.id,
        ),
    )
    has_earlier_event = exists(
        select(1)
        .select_from(earlier)
        .where(
            earlier.event_type == InboundEventType.MESSAGE,
            earlier.sender_identifier == InboundWhatsAppEvent.sender_identifier,
            earlier.id != InboundWhatsAppEvent.id,
            earlier.status.in_(queued_statuses),
            earlier_than_candidate,
        )
    )
    another_active_processing_event = exists(
        select(1)
        .select_from(earlier)
        .where(
            earlier.event_type == InboundEventType.MESSAGE,
            earlier.sender_identifier == InboundWhatsAppEvent.sender_identifier,
            earlier.id != InboundWhatsAppEvent.id,
            earlier.status == InboundEventStatus.PROCESSING,
            earlier.lease_expires_at > current_time,
        )
    )
    sender_ordering = or_(
        InboundWhatsAppEvent.event_type != InboundEventType.MESSAGE,
        and_(
            InboundWhatsAppEvent.sender_identifier.is_not(None),
            ~has_earlier_event,
            ~another_active_processing_event,
        ),
    )
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
        .where(available, sender_ordering)
        .order_by(
            InboundWhatsAppEvent.provider_timestamp.nulls_first(),
            InboundWhatsAppEvent.created_at,
            InboundWhatsAppEvent.id,
        )
        .with_for_update(of=InboundWhatsAppEvent, skip_locked=True)
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
    retryable = not isinstance(error, WhatsAppMediaDownloadError) or error.retryable
    terminal = attempt_count >= settings.whatsapp_inbox_max_attempts or not retryable
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
            InboundWhatsAppEvent.lease_expires_at > now,
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


def _renew_inbound_event_lease(
    event_id: uuid.UUID,
    *,
    worker_id: str,
) -> bool:
    settings = get_settings()
    db = SessionLocal()
    try:
        now = _current_utc_time(db)
        result = db.execute(
            update(InboundWhatsAppEvent)
            .where(
                InboundWhatsAppEvent.id == event_id,
                InboundWhatsAppEvent.status == InboundEventStatus.PROCESSING,
                InboundWhatsAppEvent.claimed_by == worker_id,
                InboundWhatsAppEvent.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now
                + timedelta(seconds=settings.whatsapp_inbox_lease_seconds),
                updated_at=now,
            )
        )
        db.commit()
        return result.rowcount == 1
    except Exception as error:  # noqa: BLE001
        db.rollback()
        logger.warning(
            "Inbound lease renewal failed: event_id=%s error_type=%s",
            event_id,
            type(error).__name__,
        )
        return False
    finally:
        db.close()


async def _maintain_inbound_event_lease(
    event_id: uuid.UUID,
    *,
    worker_id: str,
    stop: asyncio.Event,
) -> None:
    settings = get_settings()
    interval = max(0.1, settings.whatsapp_inbox_lease_seconds / 3)
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            if not _renew_inbound_event_lease(event_id, worker_id=worker_id):
                return


async def process_one_inbound_event(
    db: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> bool:
    current_time = _current_utc_time(db, now)

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
    db.commit()

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _maintain_inbound_event_lease(
            event_id,
            worker_id=worker_id,
            stop=heartbeat_stop,
        )
    )

    try:
        if event_type == InboundEventType.MESSAGE:
            message = IncomingWhatsAppMessage.model_validate(event_payload)
            await process_incoming_message(
                db=db,
                message=message,
                inbox_event_id=event_id,
                worker_id=worker_id,
            )
        elif event_type == InboundEventType.STATUS_CALLBACK:
            status_event = NormalizedStatus.model_validate(event_payload)
            lock_inbound_event_claim(
                db,
                event_id=event_id,
                worker_id=worker_id,
            )
            process_whatsapp_status_callback(db=db, status_event=status_event)
        else:
            raise ValueError("Unknown inbound event type")
    except InboundEventLeaseLostError:
        db.rollback()
        logger.info(
            "Inbound event lease lost: event_id=%s worker_id=%s",
            event_id,
            worker_id,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        db.rollback()
        failure_time = current_time if now is not None else _current_utc_time(db)
        _record_failure(
            db,
            event_id=event_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            error=error,
            now=failure_time,
        )
        logger.warning(
            "Inbound event processing failed: event_id=%s event_type=%s error_type=%s",
            event_id,
            event_type.value,
            type(error).__name__,
        )
        return True
    finally:
        heartbeat_stop.set()
        if not heartbeat_task.done():
            heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)

    completed_at = _current_utc_time(db)
    completion = db.execute(
        update(InboundWhatsAppEvent)
        .where(
            InboundWhatsAppEvent.id == event_id,
            InboundWhatsAppEvent.status == InboundEventStatus.PROCESSING,
            InboundWhatsAppEvent.claimed_by == worker_id,
            InboundWhatsAppEvent.lease_expires_at > completed_at,
        )
        .values(
            status=InboundEventStatus.COMPLETED,
            processed_at=completed_at,
            lease_expires_at=None,
            claimed_by=None,
            last_error=None,
            updated_at=completed_at,
        )
    )
    if completion.rowcount != 1:
        db.rollback()
        logger.info(
            "Inbound event completion skipped after lease loss: event_id=%s worker_id=%s",
            event_id,
            worker_id,
        )
        return True
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
