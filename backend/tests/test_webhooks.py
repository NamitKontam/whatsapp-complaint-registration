import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes import webhooks
from app.db.session import get_db
from app.main import app
from app.models.outbound_whatsapp_message import OutboundMessageStatus
from app.services.whatsapp_media_service import WhatsAppMediaDownloadError

META_APP_SECRET = "test-meta-app-secret"

PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "100200300",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "messages": [
                            {
                                "from": "919876543210",
                                "id": "wamid.test_webhook_message",
                                "timestamp": "1732345678",
                                "type": "text",
                                "text": {"body": "Water leakage problem."},
                            }
                        ],
                    },
                }
            ],
        }
    ],
}


@pytest.fixture
def webhook_client(monkeypatch):
    """Create a test client with mocked processing and database access."""

    db = MagicMock()

    app.dependency_overrides[get_db] = lambda: db

    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(meta_app_secret=META_APP_SECRET),
    )

    monkeypatch.setattr(
        webhooks,
        "process_incoming_message",
        AsyncMock(),
    )

    monkeypatch.setattr(
        webhooks,
        "process_whatsapp_status_callback",
        MagicMock(),
    )

    monkeypatch.setattr(
        webhooks,
        "dispatch_outbound_message",
        AsyncMock(),
    )

    yield TestClient(app), db

    app.dependency_overrides.clear()


def make_signature(body: bytes) -> str:
    digest = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return f"sha256={digest}"


def send_webhook(client: TestClient, payload=PAYLOAD):
    body = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode("utf-8")

    return client.post(
        "/api/webhooks/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": make_signature(body),
        },
    )


@pytest.mark.parametrize(
    ("status", "should_dispatch"),
    [
        (OutboundMessageStatus.PENDING, True),
        (OutboundMessageStatus.RETRY_SCHEDULED, True),
        (OutboundMessageStatus.FAILED, False),
        (OutboundMessageStatus.SENT, False),
        (None, False),
    ],
)
def test_webhook_dispatches_only_pending_or_due_retry_candidates(
    webhook_client,
    status,
    should_dispatch,
):
    client, db = webhook_client

    inbound_message_id = uuid.uuid4()

    result = SimpleNamespace(message=SimpleNamespace(id=inbound_message_id))

    webhooks.process_incoming_message.return_value = result

    outbound_message = None

    if status is not None:
        outbound_message = SimpleNamespace(
            id=uuid.uuid4(),
            status=status,
        )

    db.scalar.return_value = outbound_message

    response = send_webhook(client)

    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "processed_messages": 1,
    }

    webhooks.process_incoming_message.assert_awaited_once()
    webhooks.process_whatsapp_status_callback.assert_not_called()

    if should_dispatch:
        webhooks.dispatch_outbound_message.assert_awaited_once_with(
            db=db,
            outbound_message_id=outbound_message.id,
        )
    else:
        webhooks.dispatch_outbound_message.assert_not_awaited()


def test_webhook_processes_status_callbacks_without_inbound_messages(
    webhook_client,
):
    client, _ = webhook_client
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.outbound-status",
                                    "status": "delivered",
                                    "timestamp": "1795694400",
                                    "recipient_id": "919876543210",
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }

    response = send_webhook(client, status_payload)

    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "processed_messages": 0,
    }
    webhooks.process_incoming_message.assert_not_awaited()
    webhooks.process_whatsapp_status_callback.assert_called_once()
    event = webhooks.process_whatsapp_status_callback.call_args.kwargs["status_event"]
    assert event.whatsapp_message_id == "wamid.outbound-status"
    assert event.status == "delivered"


def test_webhook_returns_retryable_error_for_media_download_failure(
    webhook_client,
):
    client, _ = webhook_client
    webhooks.process_incoming_message.side_effect = WhatsAppMediaDownloadError(
        "Download failed"
    )

    response = send_webhook(client)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Incoming WhatsApp media could not be downloaded"
    }
    webhooks.dispatch_outbound_message.assert_not_awaited()


def test_duplicate_unlinked_unsupported_webhook_does_not_resend_reply(
    webhook_client,
):
    client, db = webhook_client
    unsupported_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": "wamid.unsupported_webhook",
                                    "timestamp": "1732345678",
                                    "type": "sticker",
                                    "sticker": {"id": "sticker-id"},
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }
    unsupported_inbound_id = uuid.uuid4()
    result = SimpleNamespace(
        message=None,
        unsupported_inbound_message_id=unsupported_inbound_id,
    )
    webhooks.process_incoming_message.side_effect = [result, result]
    outbound_message = SimpleNamespace(
        id=uuid.uuid4(),
        status=OutboundMessageStatus.PENDING,
    )
    db.scalar.side_effect = [outbound_message, outbound_message]

    first_response = send_webhook(client, unsupported_payload)
    outbound_message.status = OutboundMessageStatus.SENT
    duplicate_response = send_webhook(client, unsupported_payload)

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert webhooks.dispatch_outbound_message.await_count == 1
    statement = db.scalar.call_args_list[0].args[0]
    assert "unsupported_inbound_message_id" in str(statement)
