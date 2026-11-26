import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.complaint import Complaint, ComplaintStatus, ConversationState
from app.models.complaint_message import ComplaintMessage, MessageType
from app.models.inbound_whatsapp_event import (
    InboundEventStatus,
    InboundEventType,
    InboundWhatsAppEvent,
)
from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.models.pending_whatsapp_status import PendingWhatsAppStatus
from app.models.user import User
from app.schemas.whatsapp_message import IncomingWhatsAppMessage, MessageKind
from app.services import whatsapp_inbox_worker
from app.services.whatsapp_inbox_service import persist_webhook_events
from app.services.whatsapp_inbox_worker import (
    claim_next_inbound_event,
    process_one_inbound_event,
)
from app.services.whatsapp_payload_service import (
    NormalizedStatus,
    ParsedWebhookPayload,
)


def _settings(
    *,
    max_attempts=3,
    retry_base_seconds=10,
    retry_max_seconds=60,
    lease_seconds=30,
):
    return SimpleNamespace(
        whatsapp_inbox_max_attempts=max_attempts,
        whatsapp_inbox_retry_base_seconds=retry_base_seconds,
        whatsapp_inbox_retry_max_seconds=retry_max_seconds,
        whatsapp_inbox_lease_seconds=lease_seconds,
        whatsapp_inbox_poll_interval_seconds=1,
    )


def _message(message_id="wamid.inbox-worker"):
    return IncomingWhatsAppMessage(
        whatsapp_number="919876543210",
        whatsapp_message_id=message_id,
        timestamp=datetime(2026, 11, 26, 7, 0, tzinfo=UTC),
        message_type=MessageKind.TEXT,
        text_content="Water is leaking near the park.",
        raw_type="text",
    )


def _enqueue_message(db_session, message=None):
    message = message or _message()
    parsed = ParsedWebhookPayload(messages=[message])
    assert persist_webhook_events(db_session, parsed) == 1
    return db_session.scalar(
        select(InboundWhatsAppEvent).where(
            InboundWhatsAppEvent.whatsapp_message_id == message.whatsapp_message_id
        )
    )


def _create_outbound(db_session, message_id):
    user = User(whatsapp_number="+919876543210")
    db_session.add(user)
    db_session.flush()
    complaint = Complaint(
        user_id=user.id,
        status=ComplaintStatus.DRAFT,
        conversation_state=ConversationState.AWAITING_COMPLAINT,
    )
    db_session.add(complaint)
    db_session.flush()
    inbound = ComplaintMessage(
        complaint_id=complaint.id,
        whatsapp_message_id=f"wamid.inbound-{uuid.uuid4()}",
        message_type=MessageType.TEXT,
        text_content="Leak.",
    )
    db_session.add(inbound)
    db_session.flush()
    outbound = OutboundWhatsAppMessage(
        inbound_message_id=inbound.id,
        complaint_id=complaint.id,
        recipient=user.whatsapp_number,
        body="Reply.",
        status=OutboundMessageStatus.SENT,
        attempts=1,
        whatsapp_message_id=message_id,
    )
    db_session.add(outbound)
    db_session.flush()
    return outbound


def test_inbox_persistence_deduplicates_messages_and_status_callbacks(db_session):
    message = _message("wamid.persist-once")
    status = NormalizedStatus(
        status="delivered",
        whatsapp_message_id="wamid.outbound-status",
        recipient_id="919876543210",
        timestamp="1795694400",
        errors=[{"code": 131047, "message": "details not needed"}],
    )
    parsed = ParsedWebhookPayload(messages=[message], statuses=[status])

    assert persist_webhook_events(db_session, parsed) == 2
    assert persist_webhook_events(db_session, parsed) == 0

    events = list(db_session.scalars(select(InboundWhatsAppEvent)))
    assert len(events) == 2
    persisted_status = next(
        event
        for event in events
        if event.event_type == InboundEventType.STATUS_CALLBACK
    )
    assert persisted_status.event_payload["errors"] == [{"code": 131047}]
    assert persisted_status.status == InboundEventStatus.PENDING


@pytest.mark.anyio
async def test_worker_processes_inbound_message_and_queues_one_outbox_reply(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    event = _enqueue_message(db_session)
    now = event.available_at + timedelta(seconds=1)

    assert await process_one_inbound_event(
        db_session,
        worker_id="worker-a",
        now=now,
    )

    db_session.refresh(event)
    assert event.status == InboundEventStatus.COMPLETED
    assert event.attempt_count == 1
    assert event.processed_at is not None
    assert (
        db_session.scalar(
            select(ComplaintMessage).where(
                ComplaintMessage.whatsapp_message_id == event.whatsapp_message_id
            )
        )
        is not None
    )
    assert (
        db_session.scalar(select(OutboundWhatsAppMessage)).inbound_message_id
        is not None
    )


@pytest.mark.anyio
async def test_worker_processes_status_callback_with_existing_reconciliation_service(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    outbound = _create_outbound(db_session, "wamid.status-worker")
    status = NormalizedStatus(
        status="delivered",
        whatsapp_message_id="wamid.status-worker",
        recipient_id=outbound.recipient,
        timestamp="1795694400",
    )
    parsed = ParsedWebhookPayload(statuses=[status])
    assert persist_webhook_events(db_session, parsed) == 1
    event = db_session.scalar(select(InboundWhatsAppEvent))
    now = event.available_at + timedelta(seconds=1)

    assert await process_one_inbound_event(
        db_session,
        worker_id="status-worker",
        now=now,
    )

    db_session.refresh(event)
    db_session.refresh(outbound)
    assert event.status == InboundEventStatus.COMPLETED
    assert outbound.provider_status == "delivered"


@pytest.mark.anyio
async def test_worker_stages_status_callback_until_provider_id_is_known(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    status = NormalizedStatus(
        status="delivered",
        whatsapp_message_id="wamid.not-yet-linked",
        recipient_id="919876543210",
        timestamp="1795694400",
    )
    assert (
        persist_webhook_events(
            db_session,
            ParsedWebhookPayload(statuses=[status]),
        )
        == 1
    )
    event = db_session.scalar(select(InboundWhatsAppEvent))
    now = event.available_at + timedelta(seconds=1)

    assert await process_one_inbound_event(
        db_session,
        worker_id="status-staging-worker",
        now=now,
    )

    db_session.refresh(event)
    staged = db_session.scalar(select(PendingWhatsAppStatus))
    assert event.status == InboundEventStatus.COMPLETED
    assert staged is not None
    assert staged.whatsapp_message_id == "wamid.not-yet-linked"


@pytest.mark.anyio
async def test_worker_retries_with_bounded_backoff_then_marks_terminal_failure(
    db_session,
    monkeypatch,
):
    settings = _settings(max_attempts=2, retry_base_seconds=10, retry_max_seconds=15)
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: settings)
    event = _enqueue_message(db_session, _message("wamid.retry-terminal"))
    now = event.available_at + timedelta(seconds=1)

    async def fail_processing(*, db, message):
        raise RuntimeError("sensitive content is not persisted")

    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "process_incoming_message",
        fail_processing,
    )
    assert await process_one_inbound_event(
        db_session,
        worker_id="retry-worker",
        now=now,
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.RETRY_SCHEDULED
    assert event.available_at == now + timedelta(seconds=10)
    assert event.last_error == "RuntimeError"
    assert "sensitive content" not in event.last_error

    assert await process_one_inbound_event(
        db_session,
        worker_id="retry-worker",
        now=event.available_at,
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.FAILED
    assert event.attempt_count == 2
    assert event.lease_expires_at is None


@pytest.mark.anyio
async def test_expired_worker_lease_is_reclaimed(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    event = _enqueue_message(db_session, _message("wamid.lease-recovery"))
    now = event.available_at + timedelta(seconds=1)
    event.status = InboundEventStatus.PROCESSING
    event.attempt_count = 1
    event.claimed_by = "interrupted-worker"
    event.lease_expires_at = now - timedelta(seconds=1)
    db_session.flush()

    assert await process_one_inbound_event(
        db_session,
        worker_id="recovery-worker",
        now=now,
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.COMPLETED
    assert event.attempt_count == 2
    assert event.claimed_by is None


@pytest.mark.anyio
async def test_reprocessing_after_business_commit_does_not_duplicate_complaint_or_outbox(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    event = _enqueue_message(db_session, _message("wamid.crash-recovery"))
    now = event.available_at + timedelta(seconds=1)
    original_process = whatsapp_inbox_worker.process_incoming_message
    calls = 0

    async def crash_after_business_commit(*, db, message):
        nonlocal calls
        calls += 1
        result = await original_process(db=db, message=message)
        if calls == 1:
            raise RuntimeError("simulated worker interruption")
        return result

    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "process_incoming_message",
        crash_after_business_commit,
    )

    assert await process_one_inbound_event(
        db_session,
        worker_id="crash-worker",
        now=now,
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.RETRY_SCHEDULED
    assert (
        db_session.scalar(
            select(ComplaintMessage).where(
                ComplaintMessage.whatsapp_message_id == event.whatsapp_message_id
            )
        )
        is not None
    )
    assert len(list(db_session.scalars(select(OutboundWhatsAppMessage)))) == 1

    assert await process_one_inbound_event(
        db_session,
        worker_id="restarted-worker",
        now=event.available_at,
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.COMPLETED
    assert len(list(db_session.scalars(select(ComplaintMessage)))) == 1
    assert len(list(db_session.scalars(select(OutboundWhatsAppMessage)))) == 1


def test_parallel_workers_claim_a_single_event_once(test_engine):
    message = _message(f"wamid.claim-race-{uuid.uuid4()}")
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    event = InboundWhatsAppEvent(
        id=event_id,
        event_key=uuid.uuid4().hex + uuid.uuid4().hex,
        event_type=InboundEventType.MESSAGE,
        status=InboundEventStatus.PENDING,
        whatsapp_message_id=message.whatsapp_message_id,
        sender_identifier=message.whatsapp_number,
        provider_timestamp=message.timestamp,
        event_payload=message.model_dump(mode="json"),
        available_at=now,
    )
    with Session(test_engine) as setup_session:
        setup_session.add(event)
        setup_session.commit()

    barrier = threading.Barrier(2)

    def claim(worker_id):
        with Session(test_engine) as session:
            barrier.wait(timeout=5)
            return claim_next_inbound_event(
                session,
                worker_id=worker_id,
                now=now,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(claim, ("worker-one", "worker-two")))
        assert claims.count(event_id) == 1
        assert claims.count(None) == 1
    finally:
        with Session(test_engine) as cleanup_session:
            cleanup_session.query(InboundWhatsAppEvent).filter_by(id=event_id).delete()
            cleanup_session.commit()
