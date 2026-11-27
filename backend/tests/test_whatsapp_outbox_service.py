from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
)
from app.services.whatsapp_outbox_service import (
    dispatch_outbound_message,
)
from app.services.whatsapp_sender_service import (
    WhatsAppRetryableSendError,
    WhatsAppSendError,
    WhatsAppUncertainSendError,
)

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def outbound_message():
    return SimpleNamespace(
        id=uuid4(),
        recipient="919999999999",
        body="Your complaint has been registered.",
        interactive_buttons=None,
        status=OutboundMessageStatus.PENDING,
        attempts=0,
        last_error=None,
        whatsapp_message_id=None,
        sent_at=None,
        next_attempt_at=None,
        last_attempt_at=None,
        provider_status=None,
        provider_status_timestamp=None,
    )


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    configured = SimpleNamespace(
        whatsapp_outbox_max_attempts=5,
        whatsapp_outbox_retry_base_seconds=10,
        whatsapp_outbox_retry_max_seconds=25,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.get_settings",
        lambda: configured,
    )
    return configured


@pytest.mark.anyio
async def test_dispatch_returns_false_when_message_does_not_exist(
    db,
    monkeypatch,
):
    db.scalar.return_value = None

    send_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(db, uuid4())

    assert result is False
    send_mock.assert_not_awaited()
    db.commit.assert_not_called()


@pytest.mark.anyio
async def test_dispatch_does_not_resend_already_sent_message(
    db,
    outbound_message,
    monkeypatch,
):
    outbound_message.status = OutboundMessageStatus.SENT
    db.scalar.return_value = outbound_message

    send_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
    )

    assert result is True
    send_mock.assert_not_awaited()
    db.commit.assert_not_called()


@pytest.mark.anyio
async def test_legacy_pending_attempt_is_marked_uncertain_not_resent(
    db,
    outbound_message,
    monkeypatch,
):
    outbound_message.attempts = 1
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW,
    )

    assert result is False
    assert outbound_message.status == OutboundMessageStatus.UNCERTAIN
    assert outbound_message.attempts == 1
    send_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_dispatch_marks_message_sent_on_success(
    db,
    outbound_message,
    monkeypatch,
):
    db.scalar.return_value = outbound_message
    call_order = []
    db.commit.side_effect = lambda: call_order.append("commit")

    async def send_and_confirm_claim(**_):
        call_order.append("http")
        assert outbound_message.status == OutboundMessageStatus.UNCERTAIN
        assert outbound_message.attempts == 1
        return {"messages": [{"id": "wamid.test-message-id"}]}

    send_mock = AsyncMock(side_effect=send_and_confirm_claim)
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW,
    )

    assert result is True
    assert outbound_message.status == OutboundMessageStatus.SENT
    assert outbound_message.attempts == 1
    assert outbound_message.whatsapp_message_id == "wamid.test-message-id"
    assert isinstance(outbound_message.sent_at, datetime)
    assert outbound_message.sent_at.tzinfo == UTC
    assert outbound_message.last_error is None

    send_mock.assert_awaited_once_with(
        recipient=outbound_message.recipient,
        text=outbound_message.body,
    )
    assert db.commit.call_count == 2
    assert call_order == ["commit", "http", "commit"]


@pytest.mark.anyio
async def test_dispatch_sends_interactive_outbox_message(
    db,
    outbound_message,
    monkeypatch,
):
    outbound_message.interactive_buttons = [
        {"id": "complaint_add_more", "title": "Yes, add details"},
        {"id": "complaint_submit", "title": "No, submit"},
    ]
    db.scalar.return_value = outbound_message

    interactive_send = AsyncMock(
        return_value={"messages": [{"id": "wamid.interactive"}]}
    )
    text_send = AsyncMock()
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_interactive_message",
        interactive_send,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        text_send,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW,
    )

    assert result is True
    interactive_send.assert_awaited_once_with(
        recipient=outbound_message.recipient,
        text=outbound_message.body,
        buttons=outbound_message.interactive_buttons,
    )
    text_send.assert_not_awaited()
    assert outbound_message.status == OutboundMessageStatus.SENT
    assert outbound_message.whatsapp_message_id == "wamid.interactive"


@pytest.mark.anyio
async def test_dispatch_schedules_and_retries_due_interactive_message(
    db,
    outbound_message,
    monkeypatch,
):
    outbound_message.interactive_buttons = [
        {"id": "complaint_add_more", "title": "Yes, add details"},
        {"id": "complaint_submit", "title": "No, submit"},
    ]
    db.scalar.return_value = outbound_message

    interactive_send = AsyncMock(
        side_effect=[
            WhatsAppRetryableSendError("Connection failed before sending."),
            {"messages": [{"id": "wamid.interactive-retry"}]},
        ]
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_interactive_message",
        interactive_send,
    )

    first_result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW,
    )

    assert first_result is False
    assert outbound_message.status == OutboundMessageStatus.RETRY_SCHEDULED
    assert outbound_message.attempts == 1
    assert outbound_message.last_error == "Connection failed before sending."
    assert outbound_message.next_attempt_at == NOW + timedelta(seconds=10)

    early_retry = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(seconds=9),
    )
    assert early_retry is False
    assert interactive_send.await_count == 1

    retry_result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(seconds=10),
    )

    assert retry_result is True
    assert outbound_message.status == OutboundMessageStatus.SENT
    assert outbound_message.attempts == 2
    assert outbound_message.last_error is None
    assert outbound_message.whatsapp_message_id == "wamid.interactive-retry"
    assert interactive_send.await_count == 2
    assert db.commit.call_count == 4


@pytest.mark.anyio
async def test_dispatch_marks_message_failed_on_sender_error(
    db,
    outbound_message,
    monkeypatch,
):
    db.scalar.return_value = outbound_message

    send_mock = AsyncMock(side_effect=WhatsAppSendError("WhatsApp API request failed."))
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW,
    )

    assert result is False
    assert outbound_message.status == OutboundMessageStatus.FAILED
    assert outbound_message.attempts == 1
    assert outbound_message.last_error == "WhatsApp API request failed."
    assert outbound_message.whatsapp_message_id is None
    assert outbound_message.sent_at is None

    send_mock.assert_awaited_once()
    assert db.commit.call_count == 2


@pytest.mark.anyio
async def test_retry_backoff_increases_and_is_capped(
    db,
    outbound_message,
    monkeypatch,
    settings,
):
    settings.whatsapp_outbox_max_attempts = 5
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(
        side_effect=WhatsAppRetryableSendError("Pre-send connection failure.")
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    await dispatch_outbound_message(db, outbound_message.id, now=NOW)
    assert outbound_message.next_attempt_at == NOW + timedelta(seconds=10)

    outbound_message.status = OutboundMessageStatus.RETRY_SCHEDULED
    await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(seconds=10),
    )
    assert outbound_message.next_attempt_at == NOW + timedelta(seconds=30)

    outbound_message.status = OutboundMessageStatus.RETRY_SCHEDULED
    await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(seconds=30),
    )
    assert outbound_message.next_attempt_at == NOW + timedelta(seconds=55)
    assert send_mock.await_count == 3


@pytest.mark.anyio
async def test_retry_after_is_respected(
    db,
    outbound_message,
    monkeypatch,
    settings,
):
    settings.whatsapp_outbox_retry_max_seconds = 3600
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(
        side_effect=WhatsAppRetryableSendError(
            "WhatsApp API rate-limited the send request.",
            retry_after="120",
        )
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    await dispatch_outbound_message(db, outbound_message.id, now=NOW)

    assert outbound_message.status == OutboundMessageStatus.RETRY_SCHEDULED
    assert outbound_message.next_attempt_at == NOW + timedelta(seconds=120)


@pytest.mark.anyio
async def test_http_date_retry_after_is_respected(
    db,
    outbound_message,
    monkeypatch,
    settings,
):
    settings.whatsapp_outbox_retry_max_seconds = 3600
    db.scalar.return_value = outbound_message
    retry_at = NOW + timedelta(seconds=90)
    send_mock = AsyncMock(
        side_effect=WhatsAppRetryableSendError(
            "WhatsApp API rate-limited the send request.",
            retry_after=format_datetime(retry_at, usegmt=True),
        )
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    await dispatch_outbound_message(db, outbound_message.id, now=NOW)

    assert outbound_message.next_attempt_at == retry_at


@pytest.mark.anyio
async def test_unreasonably_large_retry_after_is_safely_bounded(
    db,
    outbound_message,
    monkeypatch,
    settings,
):
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(
        side_effect=WhatsAppRetryableSendError(
            "WhatsApp API rate-limited the send request.",
            retry_after="1e100",
        )
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    await dispatch_outbound_message(db, outbound_message.id, now=NOW)

    assert outbound_message.next_attempt_at == NOW + timedelta(
        seconds=settings.whatsapp_outbox_retry_max_seconds
    )


@pytest.mark.anyio
async def test_retry_limit_moves_message_to_terminal_failure(
    db,
    outbound_message,
    monkeypatch,
    settings,
):
    settings.whatsapp_outbox_max_attempts = 5
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(
        side_effect=[
            WhatsAppRetryableSendError("Pre-send connection failure.")
            for _ in range(settings.whatsapp_outbox_max_attempts)
        ]
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    retry_at = NOW
    for attempt in range(1, settings.whatsapp_outbox_max_attempts + 1):
        result = await dispatch_outbound_message(
            db,
            outbound_message.id,
            now=retry_at,
        )
        assert result is False
        assert outbound_message.attempts == attempt
        if attempt < settings.whatsapp_outbox_max_attempts:
            assert outbound_message.status == OutboundMessageStatus.RETRY_SCHEDULED
            retry_at = outbound_message.next_attempt_at
        else:
            assert outbound_message.status == OutboundMessageStatus.FAILED

    assert outbound_message.status == OutboundMessageStatus.FAILED
    assert outbound_message.attempts == settings.whatsapp_outbox_max_attempts
    assert outbound_message.next_attempt_at is None
    assert send_mock.await_count == settings.whatsapp_outbox_max_attempts

    await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(days=1),
    )
    assert send_mock.await_count == settings.whatsapp_outbox_max_attempts


@pytest.mark.anyio
async def test_permanent_failure_is_terminal(
    db,
    outbound_message,
    monkeypatch,
):
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(
        side_effect=WhatsAppSendError("WhatsApp API rejected the request.")
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    await dispatch_outbound_message(db, outbound_message.id, now=NOW)

    assert outbound_message.status == OutboundMessageStatus.FAILED
    assert outbound_message.next_attempt_at is None

    retry_result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(days=1),
    )
    assert retry_result is False
    assert send_mock.await_count == 1


@pytest.mark.anyio
async def test_uncertain_delivery_is_never_automatically_retried(
    db,
    outbound_message,
    monkeypatch,
):
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(
        side_effect=WhatsAppUncertainSendError(
            "The WhatsApp request timed out after transmission may have started."
        )
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    first_result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW,
    )
    second_result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(days=1),
    )

    assert first_result is False
    assert second_result is False
    assert outbound_message.status == OutboundMessageStatus.UNCERTAIN
    assert outbound_message.attempts == 1
    assert outbound_message.next_attempt_at is None
    assert send_mock.await_count == 1


@pytest.mark.anyio
async def test_success_without_provider_id_is_uncertain(
    db,
    outbound_message,
    monkeypatch,
):
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(return_value={"messages": []})
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW,
    )

    assert result is False
    assert outbound_message.status == OutboundMessageStatus.UNCERTAIN
    assert outbound_message.whatsapp_message_id is None
    assert outbound_message.next_attempt_at is None
    assert send_mock.await_count == 1


@pytest.mark.anyio
async def test_unhandled_failure_leaves_attempt_uncertain_for_crash_safety(
    db,
    outbound_message,
    monkeypatch,
):
    db.scalar.return_value = outbound_message
    send_mock = AsyncMock(side_effect=RuntimeError("simulated process failure"))
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    with pytest.raises(RuntimeError, match="simulated process failure"):
        await dispatch_outbound_message(db, outbound_message.id, now=NOW)

    assert outbound_message.status == OutboundMessageStatus.UNCERTAIN
    assert outbound_message.attempts == 1

    await dispatch_outbound_message(
        db,
        outbound_message.id,
        now=NOW + timedelta(days=1),
    )
    assert send_mock.await_count == 1
