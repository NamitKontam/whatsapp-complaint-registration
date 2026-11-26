import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.complaint import Complaint, ComplaintStatus, ConversationState
from app.models.complaint_message import ComplaintMessage, MessageType
from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.models.pending_whatsapp_status import PendingWhatsAppStatus
from app.models.user import User
from app.services.whatsapp_outbox_service import dispatch_due_outbound_messages
from app.services.whatsapp_payload_service import NormalizedStatus
from app.services.whatsapp_status_service import (
    process_whatsapp_status_callback,
    reconcile_pending_whatsapp_statuses,
)


def create_outbound_message(
    db_session,
    *,
    whatsapp_message_id=None,
    status=OutboundMessageStatus.SENT,
):
    user = User(whatsapp_number=f"9199{uuid.uuid4().int % 10**10:010d}")
    db_session.add(user)
    db_session.flush()

    complaint = Complaint(
        user_id=user.id,
        status=ComplaintStatus.DRAFT,
        conversation_state=ConversationState.AWAITING_COMPLAINT,
    )
    db_session.add(complaint)
    db_session.flush()

    inbound_message = ComplaintMessage(
        complaint_id=complaint.id,
        whatsapp_message_id=f"wamid.inbound-{uuid.uuid4()}",
        message_type=MessageType.TEXT,
        text_content="Water leak.",
    )
    db_session.add(inbound_message)
    db_session.flush()

    outbound_message = OutboundWhatsAppMessage(
        inbound_message_id=inbound_message.id,
        complaint_id=complaint.id,
        recipient=user.whatsapp_number,
        body="Your complaint has been received.",
        status=status,
        attempts=1,
        whatsapp_message_id=whatsapp_message_id,
    )
    db_session.add(outbound_message)
    db_session.flush()
    return outbound_message


def make_status(
    status,
    *,
    whatsapp_message_id="wamid.provider-id",
    recipient_id="919876543210",
    timestamp="1795694400",
    errors=None,
):
    return NormalizedStatus(
        status=status,
        whatsapp_message_id=whatsapp_message_id,
        recipient_id=recipient_id,
        timestamp=timestamp,
        errors=errors,
    )


def test_status_callback_updates_matching_outbox_and_is_idempotent(db_session):
    outbound_message = create_outbound_message(
        db_session,
        whatsapp_message_id="wamid.provider-id",
    )
    event = make_status("delivered", recipient_id=outbound_message.recipient)

    assert process_whatsapp_status_callback(db_session, event) is True
    assert outbound_message.provider_status == "delivered"
    assert outbound_message.status == OutboundMessageStatus.SENT
    first_timestamp = outbound_message.provider_status_timestamp

    assert process_whatsapp_status_callback(db_session, event) is False
    assert outbound_message.provider_status == "delivered"
    assert outbound_message.provider_status_timestamp == first_timestamp
    assert (
        db_session.scalar(
            select(PendingWhatsAppStatus).where(
                PendingWhatsAppStatus.whatsapp_message_id == "wamid.provider-id"
            )
        )
        is None
    )


def test_status_callback_received_before_provider_id_is_reconciled(db_session):
    outbound_message = create_outbound_message(
        db_session,
        status=OutboundMessageStatus.UNCERTAIN,
    )
    event = make_status(
        "delivered",
        recipient_id=outbound_message.recipient,
    )

    assert process_whatsapp_status_callback(db_session, event) is False
    assert process_whatsapp_status_callback(db_session, event) is False
    assert (
        len(
            list(
                db_session.scalars(
                    select(PendingWhatsAppStatus).where(
                        PendingWhatsAppStatus.whatsapp_message_id == "wamid.provider-id"
                    )
                )
            )
        )
        == 1
    )

    outbound_message.whatsapp_message_id = "wamid.provider-id"
    outbound_message.status = OutboundMessageStatus.SENT
    db_session.commit()

    assert (
        reconcile_pending_whatsapp_statuses(
            db_session,
            "wamid.provider-id",
        )
        == 1
    )
    assert outbound_message.provider_status == "delivered"
    assert (
        db_session.scalar(
            select(PendingWhatsAppStatus).where(
                PendingWhatsAppStatus.whatsapp_message_id == "wamid.provider-id"
            )
        )
        is None
    )


def test_known_callback_reconciles_any_staged_statuses(db_session):
    outbound_message = create_outbound_message(
        db_session,
        whatsapp_message_id="wamid.provider-id",
    )
    outbound_message.provider_status = "delivered"
    outbound_message.provider_status_timestamp = datetime.fromtimestamp(
        1795694401,
        tz=UTC,
    )
    db_session.add(
        PendingWhatsAppStatus(
            whatsapp_message_id="wamid.provider-id",
            recipient_id=outbound_message.recipient,
            status="read",
            provider_timestamp="1795694402",
        )
    )
    db_session.flush()

    stale_event = make_status(
        "sent",
        recipient_id=outbound_message.recipient,
        timestamp="1795694400",
    )

    assert process_whatsapp_status_callback(db_session, stale_event) is True
    assert outbound_message.provider_status == "read"
    assert (
        db_session.scalar(
            select(PendingWhatsAppStatus).where(
                PendingWhatsAppStatus.whatsapp_message_id == "wamid.provider-id"
            )
        )
        is None
    )


def test_out_of_order_status_callbacks_do_not_regress_delivery_state(db_session):
    outbound_message = create_outbound_message(
        db_session,
        whatsapp_message_id="wamid.provider-id",
    )
    delivered = make_status(
        "delivered",
        recipient_id=outbound_message.recipient,
        timestamp="1795694401",
    )
    sent = make_status(
        "sent",
        recipient_id=outbound_message.recipient,
        timestamp="1795694400",
    )

    assert process_whatsapp_status_callback(db_session, delivered) is True
    assert process_whatsapp_status_callback(db_session, sent) is False
    assert outbound_message.provider_status == "delivered"


@pytest.mark.parametrize(
    ("current_status", "incoming_status"),
    [
        ("delivered", "sent"),
        ("delivered", "failed"),
        ("read", "sent"),
        ("read", "failed"),
    ],
)
def test_delivery_callbacks_do_not_regress_delivered_or_read(
    db_session,
    current_status,
    incoming_status,
):
    outbound_message = create_outbound_message(
        db_session,
        whatsapp_message_id="wamid.provider-id",
    )
    outbound_message.provider_status = current_status
    outbound_message.provider_status_timestamp = datetime.fromtimestamp(
        1795694401,
        tz=UTC,
    )
    event = make_status(
        incoming_status,
        recipient_id=outbound_message.recipient,
        timestamp="1795694402",
    )

    assert process_whatsapp_status_callback(db_session, event) is False
    assert outbound_message.provider_status == current_status


def test_sent_delivered_and_read_statuses_advance_monotonically(db_session):
    outbound_message = create_outbound_message(
        db_session,
        whatsapp_message_id="wamid.provider-id",
    )

    for status, timestamp in [
        ("sent", "1795694400"),
        ("delivered", "1795694401"),
        ("read", "1795694402"),
    ]:
        event = make_status(
            status,
            recipient_id=outbound_message.recipient,
            timestamp=timestamp,
        )
        assert process_whatsapp_status_callback(db_session, event) is True
        assert outbound_message.provider_status == status


def test_failed_status_retains_safe_provider_error_code(db_session):
    outbound_message = create_outbound_message(
        db_session,
        whatsapp_message_id="wamid.provider-id",
    )
    event = make_status(
        "failed",
        recipient_id=outbound_message.recipient,
        errors=[{"code": 131047, "title": "Re-engagement message"}],
    )

    assert process_whatsapp_status_callback(db_session, event) is True
    assert outbound_message.provider_status == "failed"
    assert outbound_message.status == OutboundMessageStatus.SENT
    assert outbound_message.last_error == (
        "WhatsApp reported delivery failure (error code 131047)."
    )
    assert "Re-engagement" not in outbound_message.last_error


def test_status_callback_with_mismatched_recipient_does_not_update_outbox(
    db_session,
):
    outbound_message = create_outbound_message(
        db_session,
        whatsapp_message_id="wamid.provider-id",
    )
    event = make_status(
        "read",
        recipient_id="910000000000",
    )

    assert process_whatsapp_status_callback(db_session, event) is False
    assert outbound_message.provider_status is None


@pytest.mark.anyio
async def test_due_dispatcher_sends_only_pending_and_due_retries(
    db_session,
    monkeypatch,
):
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    pending = create_outbound_message(
        db_session,
        status=OutboundMessageStatus.PENDING,
    )
    pending.attempts = 0
    due_retry = create_outbound_message(
        db_session,
        status=OutboundMessageStatus.RETRY_SCHEDULED,
    )
    due_retry.attempts = 1
    due_retry.next_attempt_at = now - timedelta(seconds=1)
    future_retry = create_outbound_message(
        db_session,
        status=OutboundMessageStatus.RETRY_SCHEDULED,
    )
    future_retry.attempts = 1
    future_retry.next_attempt_at = now + timedelta(seconds=60)
    uncertain = create_outbound_message(
        db_session,
        status=OutboundMessageStatus.UNCERTAIN,
    )
    failed = create_outbound_message(
        db_session,
        status=OutboundMessageStatus.FAILED,
    )
    db_session.flush()

    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.get_settings",
        lambda: SimpleNamespace(
            whatsapp_outbox_max_attempts=5,
            whatsapp_outbox_retry_base_seconds=30,
            whatsapp_outbox_retry_max_seconds=3600,
        ),
    )
    sender = AsyncMock(
        side_effect=[
            {"messages": [{"id": "wamid.due-pending"}]},
            {"messages": [{"id": "wamid.due-retry"}]},
        ]
    )
    monkeypatch.setattr(
        "app.services.whatsapp_outbox_service.send_whatsapp_text_message",
        sender,
    )

    dispatched = await dispatch_due_outbound_messages(db_session, now=now)

    assert dispatched == 2
    assert sender.await_count == 2
    assert pending.status == OutboundMessageStatus.SENT
    assert due_retry.status == OutboundMessageStatus.SENT
    assert future_retry.status == OutboundMessageStatus.RETRY_SCHEDULED
    assert uncertain.status == OutboundMessageStatus.UNCERTAIN
    assert failed.status == OutboundMessageStatus.FAILED
