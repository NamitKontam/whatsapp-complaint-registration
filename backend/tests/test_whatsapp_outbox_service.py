
from datetime import UTC, datetime
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
from app.services.whatsapp_sender_service import WhatsAppSendError


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def outbound_message():
    return SimpleNamespace(
        id=uuid4(),
        recipient="919999999999",
        body="Your complaint has been registered.",
        status=OutboundMessageStatus.PENDING,
        attempts=0,
        last_error=None,
        whatsapp_message_id=None,
        sent_at=None,
    )


@pytest.mark.anyio
async def test_dispatch_returns_false_when_message_does_not_exist(
    db,
    monkeypatch,
):
    db.get.return_value = None

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
    db.get.return_value = outbound_message

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
async def test_dispatch_marks_message_sent_on_success(
    db,
    outbound_message,
    monkeypatch,
):
    db.get.return_value = outbound_message

    send_mock = AsyncMock(
        return_value={
            "messages": [{"id": "wamid.test-message-id"}],
        }
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
    )

    assert result is True
    assert outbound_message.status == OutboundMessageStatus.SENT
    assert outbound_message.attempts == 1
    assert (
        outbound_message.whatsapp_message_id
        == "wamid.test-message-id"
    )
    assert isinstance(outbound_message.sent_at, datetime)
    assert outbound_message.sent_at.tzinfo == UTC
    assert outbound_message.last_error is None

    send_mock.assert_awaited_once_with(
        recipient=outbound_message.recipient,
        text=outbound_message.body,
    )
    assert db.commit.call_count == 2


@pytest.mark.anyio
async def test_dispatch_marks_message_failed_on_sender_error(
    db,
    outbound_message,
    monkeypatch,
):
    db.get.return_value = outbound_message

    send_mock = AsyncMock(
        side_effect=WhatsAppSendError("WhatsApp API request failed.")
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        send_mock,
    )

    result = await dispatch_outbound_message(
        db,
        outbound_message.id,
    )

    assert result is False
    assert outbound_message.status == OutboundMessageStatus.FAILED
    assert outbound_message.attempts == 1
    assert outbound_message.last_error == "WhatsApp API request failed."
    assert outbound_message.whatsapp_message_id is None
    assert outbound_message.sent_at is None

    send_mock.assert_awaited_once()
    assert db.commit.call_count == 2
