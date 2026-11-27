import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
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
from app.services import complaint_processing_service, whatsapp_inbox_worker
from app.services.whatsapp_inbox_service import persist_webhook_events
from app.services.whatsapp_inbox_worker import (
    claim_next_inbound_event,
    process_one_inbound_event,
)
from app.services.whatsapp_media_service import (
    WhatsAppMediaDownload,
    WhatsAppMediaDownloadError,
    WhatsAppMediaTransientError,
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


def _message(
    message_id="wamid.inbox-worker",
    *,
    whatsapp_number="919876543210",
    timestamp=None,
    text_content="Water is leaking near the park.",
):
    return IncomingWhatsAppMessage(
        whatsapp_number=whatsapp_number,
        whatsapp_message_id=message_id,
        timestamp=timestamp or datetime(2026, 11, 26, 7, 0, tzinfo=UTC),
        message_type=MessageKind.TEXT,
        text_content=text_content,
        raw_type="text",
    )


def _new_whatsapp_number():
    return f"919{uuid.uuid4().int % 10**9:09d}"


def _new_message_id(label):
    return f"wamid.{label}-{uuid.uuid4().hex}"


def _media_message(message_id, whatsapp_number="919876543210"):
    return IncomingWhatsAppMessage(
        whatsapp_number=whatsapp_number,
        whatsapp_message_id=message_id,
        timestamp=datetime(2026, 11, 26, 7, 0, tzinfo=UTC),
        message_type=MessageKind.IMAGE,
        media_id="test-media-id",
        media_mime_type="image/jpeg",
        caption="A damaged road.",
        raw_type="image",
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


def _enqueue_committed_messages(test_engine, messages):
    with Session(test_engine) as session:
        assert persist_webhook_events(
            session,
            ParsedWebhookPayload(messages=messages),
        ) == len(messages)


async def _run_one_event(test_engine, worker_id):
    with Session(test_engine) as session:
        return await process_one_inbound_event(
            session,
            worker_id=worker_id,
        )


def _cleanup_committed_messages(test_engine, messages):
    message_ids = [message.whatsapp_message_id for message in messages]
    normalized_numbers = [
        f"+{''.join(character for character in message.whatsapp_number if character.isdigit())}"
        for message in messages
    ]
    with Session(test_engine) as session:
        user_ids = list(
            session.scalars(
                select(User.id).where(User.whatsapp_number.in_(normalized_numbers))
            )
        )
        if user_ids:
            complaint_ids = select(Complaint.id).where(Complaint.user_id.in_(user_ids))
            session.execute(
                delete(OutboundWhatsAppMessage).where(
                    OutboundWhatsAppMessage.complaint_id.in_(complaint_ids)
                )
            )
            session.execute(
                delete(ComplaintMessage).where(
                    ComplaintMessage.complaint_id.in_(complaint_ids)
                )
            )
            session.execute(delete(Complaint).where(Complaint.user_id.in_(user_ids)))
            session.execute(delete(User).where(User.id.in_(user_ids)))
        session.execute(
            delete(InboundWhatsAppEvent).where(
                InboundWhatsAppEvent.whatsapp_message_id.in_(message_ids)
            )
        )
        session.commit()


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

    async def fail_processing(*, db, message, **_kwargs):
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

    async def crash_after_business_commit(
        *,
        db,
        message,
        inbox_event_id,
        worker_id,
    ):
        nonlocal calls
        calls += 1
        result = await original_process(
            db=db,
            message=message,
            inbox_event_id=inbox_event_id,
            worker_id=worker_id,
        )
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


@pytest.mark.anyio
async def test_same_sender_workers_claim_and_process_provider_timestamp_order(
    test_engine,
    monkeypatch,
):
    sender = _new_whatsapp_number()
    older = _message(
        _new_message_id("same-sender-older"),
        whatsapp_number=sender,
        timestamp=datetime(2026, 11, 26, 7, 0, tzinfo=UTC),
        text_content="Older provider event.",
    )
    newer = _message(
        _new_message_id("same-sender-newer"),
        whatsapp_number=sender,
        timestamp=datetime(2026, 11, 26, 7, 1, tzinfo=UTC),
        text_content="Newer provider event.",
    )
    messages = [newer, older]
    _enqueue_committed_messages(test_engine, messages)
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "SessionLocal",
        lambda: Session(test_engine),
    )
    original_process = whatsapp_inbox_worker.process_incoming_message
    processing_started = asyncio.Event()
    release_processing = asyncio.Event()
    processed_ids = []
    active = 0
    max_active = 0

    async def pause_before_processing(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        processed_ids.append(kwargs["message"].whatsapp_message_id)
        if len(processed_ids) == 1:
            processing_started.set()
            await release_processing.wait()
        try:
            return await original_process(**kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "process_incoming_message",
        pause_before_processing,
    )
    first_task = asyncio.create_task(_run_one_event(test_engine, "same-sender-a"))

    try:
        await asyncio.wait_for(processing_started.wait(), timeout=5)
        assert not await _run_one_event(test_engine, "same-sender-b")
        assert processed_ids == [older.whatsapp_message_id]

        release_processing.set()
        assert await first_task
        with Session(test_engine) as session:
            statuses = {
                event.whatsapp_message_id: event.status
                for event in session.scalars(
                    select(InboundWhatsAppEvent).where(
                        InboundWhatsAppEvent.whatsapp_message_id.in_(
                            [older.whatsapp_message_id, newer.whatsapp_message_id]
                        )
                    )
                )
            }
        assert statuses[older.whatsapp_message_id] == InboundEventStatus.COMPLETED
        assert statuses[newer.whatsapp_message_id] == InboundEventStatus.PENDING

        assert await _run_one_event(test_engine, "same-sender-b")
        assert processed_ids == [
            older.whatsapp_message_id,
            newer.whatsapp_message_id,
        ]
        assert max_active == 1
        with Session(test_engine) as session:
            user = session.scalar(
                select(User).where(User.whatsapp_number == f"+{sender}")
            )
            complaints = list(
                session.scalars(select(Complaint).where(Complaint.user_id == user.id))
            )
            assert len(complaints) == 1
            assert complaints[0].status == ComplaintStatus.DRAFT
            assert (
                complaints[0].conversation_state
                == ConversationState.ASKING_FOR_MORE_INFO
            )
            assert (
                len(
                    list(
                        session.scalars(
                            select(ComplaintMessage).where(
                                ComplaintMessage.complaint_id == complaints[0].id
                            )
                        )
                    )
                )
                == 2
            )
            assert (
                len(
                    list(
                        session.scalars(
                            select(OutboundWhatsAppMessage).where(
                                OutboundWhatsAppMessage.complaint_id == complaints[0].id
                            )
                        )
                    )
                )
                == 2
            )
    finally:
        release_processing.set()
        if not first_task.done():
            await first_task
        _cleanup_committed_messages(test_engine, messages)


@pytest.mark.anyio
async def test_different_senders_process_concurrently(test_engine, monkeypatch):
    messages = [
        _message(
            _new_message_id("concurrent-sender-one"),
            whatsapp_number=_new_whatsapp_number(),
        ),
        _message(
            _new_message_id("concurrent-sender-two"),
            whatsapp_number=_new_whatsapp_number(),
        ),
    ]
    _enqueue_committed_messages(test_engine, messages)
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "SessionLocal",
        lambda: Session(test_engine),
    )
    original_process = whatsapp_inbox_worker.process_incoming_message
    both_processing = asyncio.Event()
    release_processing = asyncio.Event()
    active = 0
    max_active = 0

    async def pause_each_sender(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            both_processing.set()
        try:
            await release_processing.wait()
            return await original_process(**kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "process_incoming_message",
        pause_each_sender,
    )
    workers = [
        asyncio.create_task(_run_one_event(test_engine, f"sender-worker-{index}"))
        for index in range(2)
    ]

    try:
        await asyncio.wait_for(both_processing.wait(), timeout=5)
        assert max_active == 2
        release_processing.set()
        assert await asyncio.gather(*workers) == [True, True]
    finally:
        release_processing.set()
        for worker in workers:
            if not worker.done():
                await worker
        _cleanup_committed_messages(test_engine, messages)


@pytest.mark.anyio
async def test_late_older_event_does_not_replay_completed_events(
    test_engine,
    monkeypatch,
):
    sender = _new_whatsapp_number()
    completed_message = _message(
        _new_message_id("before-late-arrival"),
        whatsapp_number=sender,
        timestamp=datetime(2026, 11, 26, 7, 5, tzinfo=UTC),
    )
    late_message = _message(
        _new_message_id("late-older-arrival"),
        whatsapp_number=sender,
        timestamp=datetime(2026, 11, 26, 7, 4, tzinfo=UTC),
    )
    _enqueue_committed_messages(test_engine, [completed_message])
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "SessionLocal",
        lambda: Session(test_engine),
    )

    try:
        assert await _run_one_event(test_engine, "late-event-worker")
        _enqueue_committed_messages(test_engine, [late_message])
        with Session(test_engine) as session:
            completed = session.scalar(
                select(InboundWhatsAppEvent).where(
                    InboundWhatsAppEvent.whatsapp_message_id
                    == completed_message.whatsapp_message_id
                )
            )
            assert completed.status == InboundEventStatus.COMPLETED
            assert len(list(session.scalars(select(OutboundWhatsAppMessage)))) == 1

        assert await _run_one_event(test_engine, "late-event-worker")
        with Session(test_engine) as session:
            assert len(list(session.scalars(select(ComplaintMessage)))) == 2
            assert len(list(session.scalars(select(OutboundWhatsAppMessage)))) == 2
    finally:
        _cleanup_committed_messages(test_engine, [completed_message, late_message])


@pytest.mark.anyio
async def test_transient_media_failure_retries_without_mutating_complaint_state(
    db_session,
    monkeypatch,
):
    settings = _settings(max_attempts=3, retry_base_seconds=10)
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: settings)
    message = _media_message(_new_message_id("media-transient-retry"))
    event = _enqueue_message(db_session, message)
    now = event.available_at + timedelta(seconds=1)
    downloads = 0

    async def fail_once_then_download(media_id, *, expected_mime_type):
        nonlocal downloads
        downloads += 1
        if downloads == 1:
            raise WhatsAppMediaTransientError("temporary network failure")
        return WhatsAppMediaDownload(
            storage_key=f"{'c' * 32}.jpg",
            media_id=media_id,
            mime_type="image/jpeg",
            size_bytes=32,
            sha256="c" * 64,
        )

    monkeypatch.setattr(
        complaint_processing_service,
        "download_whatsapp_media",
        fail_once_then_download,
    )

    assert await process_one_inbound_event(
        db_session,
        worker_id="media-retry-worker",
        now=now,
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.RETRY_SCHEDULED
    assert (
        db_session.scalar(select(User).where(User.whatsapp_number == "+919876543210"))
        is None
    )
    assert list(db_session.scalars(select(Complaint))) == []

    assert await process_one_inbound_event(
        db_session,
        worker_id="media-retry-worker",
        now=event.available_at,
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.COMPLETED
    assert downloads == 2
    assert (
        db_session.scalar(
            select(ComplaintMessage).where(
                ComplaintMessage.whatsapp_message_id == message.whatsapp_message_id
            )
        )
        is not None
    )
    assert len(list(db_session.scalars(select(OutboundWhatsAppMessage)))) == 1


@pytest.mark.anyio
async def test_permanent_media_validation_failure_is_terminal(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    message = _media_message(_new_message_id("media-permanent-failure"))
    event = _enqueue_message(db_session, message)

    async def reject_media(media_id, *, expected_mime_type):
        raise WhatsAppMediaDownloadError("permanent validation failure")

    monkeypatch.setattr(
        complaint_processing_service,
        "download_whatsapp_media",
        reject_media,
    )

    assert await process_one_inbound_event(
        db_session,
        worker_id="media-validation-worker",
        now=event.available_at + timedelta(seconds=1),
    )
    db_session.refresh(event)
    assert event.status == InboundEventStatus.FAILED
    assert event.attempt_count == 1
    assert event.last_error == "WhatsAppMediaDownloadError"
    assert (
        db_session.scalar(select(User).where(User.whatsapp_number == "+919876543210"))
        is None
    )
    assert list(db_session.scalars(select(Complaint))) == []
    assert list(db_session.scalars(select(ComplaintMessage))) == []


@pytest.mark.anyio
async def test_worker_renews_lease_without_holding_user_lock_during_media(
    test_engine,
    monkeypatch,
):
    sender = _new_whatsapp_number()
    message = _media_message(_new_message_id("media-long-running"), sender)
    _enqueue_committed_messages(test_engine, [message])
    settings = _settings(lease_seconds=1)
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "SessionLocal",
        lambda: Session(test_engine),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release(media_id, *, expected_mime_type):
        started.set()
        await release.wait()
        return WhatsAppMediaDownload(
            storage_key=f"{'d' * 32}.jpg",
            media_id=media_id,
            mime_type="image/jpeg",
            size_bytes=32,
            sha256="d" * 64,
        )

    monkeypatch.setattr(
        complaint_processing_service,
        "download_whatsapp_media",
        wait_for_release,
    )

    with Session(test_engine) as session:
        user = User(whatsapp_number=f"+{sender}")
        session.add(user)
        session.flush()
        session.add(
            Complaint(
                user_id=user.id,
                status=ComplaintStatus.DRAFT,
                conversation_state=ConversationState.AWAITING_ADDITIONAL_INFO,
            )
        )
        session.commit()

    task = asyncio.create_task(_run_one_event(test_engine, "long-media-worker"))

    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        with Session(test_engine) as session:
            event = session.scalar(
                select(InboundWhatsAppEvent).where(
                    InboundWhatsAppEvent.whatsapp_message_id
                    == message.whatsapp_message_id
                )
            )
            first_lease_expiry = event.lease_expires_at

        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
            with Session(test_engine) as session:
                event = session.scalar(
                    select(InboundWhatsAppEvent).where(
                        InboundWhatsAppEvent.whatsapp_message_id
                        == message.whatsapp_message_id
                    )
                )
                if event.lease_expires_at > first_lease_expiry:
                    break
        else:
            pytest.fail("The worker did not renew its event lease")

        with Session(test_engine) as session:
            user = session.scalar(
                select(User).where(User.whatsapp_number == f"+{sender}")
            )
            session.scalar(
                select(User).where(User.id == user.id).with_for_update(nowait=True)
            )
            assert (
                claim_next_inbound_event(
                    session,
                    worker_id="competing-worker",
                    now=datetime.now(UTC),
                )
                is None
            )

        release.set()
        assert await task
        with Session(test_engine) as session:
            event = session.scalar(
                select(InboundWhatsAppEvent).where(
                    InboundWhatsAppEvent.whatsapp_message_id
                    == message.whatsapp_message_id
                )
            )
            assert event.status == InboundEventStatus.COMPLETED
    finally:
        release.set()
        if not task.done():
            await task
        _cleanup_committed_messages(test_engine, [message])


@pytest.mark.anyio
async def test_worker_losing_lease_cannot_mutate_complaint_or_complete_event(
    test_engine,
    monkeypatch,
):
    sender = _new_whatsapp_number()
    message = _media_message(_new_message_id("lost-lease"), sender)
    _enqueue_committed_messages(test_engine, [message])
    monkeypatch.setattr(whatsapp_inbox_worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "SessionLocal",
        lambda: Session(test_engine),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_replacement(media_id, *, expected_mime_type):
        started.set()
        await release.wait()
        return WhatsAppMediaDownload(
            storage_key=f"{'e' * 32}.jpg",
            media_id=media_id,
            mime_type="image/jpeg",
            size_bytes=32,
            sha256="e" * 64,
        )

    monkeypatch.setattr(
        complaint_processing_service,
        "download_whatsapp_media",
        wait_for_replacement,
    )
    removed_media = []
    monkeypatch.setattr(
        complaint_processing_service,
        "remove_stored_whatsapp_media",
        removed_media.append,
    )
    task = asyncio.create_task(_run_one_event(test_engine, "stale-worker"))

    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        with Session(test_engine) as session:
            event = session.scalar(
                select(InboundWhatsAppEvent).where(
                    InboundWhatsAppEvent.whatsapp_message_id
                    == message.whatsapp_message_id
                )
            )
            event.claimed_by = "replacement-worker"
            event.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
            session.commit()

        release.set()
        assert await task
        with Session(test_engine) as session:
            event = session.scalar(
                select(InboundWhatsAppEvent).where(
                    InboundWhatsAppEvent.whatsapp_message_id
                    == message.whatsapp_message_id
                )
            )
            assert event.status == InboundEventStatus.PROCESSING
            assert event.claimed_by == "replacement-worker"
            assert list(session.scalars(select(Complaint))) == []
            assert list(session.scalars(select(ComplaintMessage))) == []
            assert list(session.scalars(select(OutboundWhatsAppMessage))) == []
            assert (
                session.scalar(select(User).where(User.whatsapp_number == f"+{sender}"))
                is None
            )
        assert removed_media == [f"{'e' * 32}.jpg"]
    finally:
        release.set()
        if not task.done():
            await task
        _cleanup_committed_messages(test_engine, [message])


@pytest.mark.anyio
async def test_cancelled_worker_releases_resources_and_event_is_recoverable(
    test_engine,
    monkeypatch,
):
    sender = _new_whatsapp_number()
    message = _media_message(_new_message_id("shutdown-recovery"), sender)
    _enqueue_committed_messages(test_engine, [message])
    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "get_settings",
        lambda: _settings(lease_seconds=1),
    )
    monkeypatch.setattr(
        whatsapp_inbox_worker,
        "SessionLocal",
        lambda: Session(test_engine),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def pause_download(media_id, *, expected_mime_type):
        started.set()
        await release.wait()
        return WhatsAppMediaDownload(
            storage_key=f"{'f' * 32}.jpg",
            media_id=media_id,
            mime_type="image/jpeg",
            size_bytes=32,
            sha256="f" * 64,
        )

    monkeypatch.setattr(
        complaint_processing_service,
        "download_whatsapp_media",
        pause_download,
    )
    worker = asyncio.create_task(_run_one_event(test_engine, "shutdown-worker"))

    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

        with Session(test_engine) as session:
            event = session.scalar(
                select(InboundWhatsAppEvent).where(
                    InboundWhatsAppEvent.whatsapp_message_id
                    == message.whatsapp_message_id
                )
            )
            assert event.status == InboundEventStatus.PROCESSING
            assert event.claimed_by == "shutdown-worker"
            event.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()

        release.set()
        assert await _run_one_event(test_engine, "recovery-worker")
        with Session(test_engine) as session:
            event = session.scalar(
                select(InboundWhatsAppEvent).where(
                    InboundWhatsAppEvent.whatsapp_message_id
                    == message.whatsapp_message_id
                )
            )
            assert event.status == InboundEventStatus.COMPLETED
            assert event.attempt_count == 2
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        _cleanup_committed_messages(test_engine, [message])
