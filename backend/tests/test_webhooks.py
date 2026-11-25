
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
                                "text": {
                                    "body": "Water leakage problem."
                                },
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


def send_webhook(client: TestClient):
    body = json.dumps(
        PAYLOAD,
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
        (OutboundMessageStatus.FAILED, True),
        (OutboundMessageStatus.SENT, False),
        (None, False),
    ],
)
def test_webhook_dispatches_only_pending_or_failed_messages(
    webhook_client,
    status,
    should_dispatch,
):
    client, db = webhook_client

    inbound_message_id = uuid.uuid4()

    result = SimpleNamespace(
        message=SimpleNamespace(id=inbound_message_id)
    )

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

    webhooks.process_incoming_message.assert_called_once()

    if should_dispatch:
        webhooks.dispatch_outbound_message.assert_awaited_once_with(
            db=db,
            outbound_message_id=outbound_message.id,
        )
    else:
        webhooks.dispatch_outbound_message.assert_not_awaited()
