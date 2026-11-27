import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import webhooks
from app.db.session import get_db
from app.main import app
from app.models.complaint import Complaint
from app.models.complaint_message import ComplaintMessage
from app.models.inbound_whatsapp_event import InboundWhatsAppEvent
from app.models.outbound_whatsapp_message import OutboundWhatsAppMessage

META_APP_SECRET = "test-meta-app-secret"
WHATSAPP_VERIFY_TOKEN = "test-verify-token"

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
def webhook_client(db_session, monkeypatch):
    """Create a signed webhook client backed by the PostgreSQL test session."""
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(
            meta_app_secret=META_APP_SECRET,
            whatsapp_verify_token=WHATSAPP_VERIFY_TOKEN,
        ),
    )
    yield TestClient(app), db_session
    app.dependency_overrides.clear()


def make_signature(body: bytes) -> str:
    digest = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def send_webhook(client: TestClient, payload=PAYLOAD, signature=None):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return client.post(
        "/api/webhooks/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature or make_signature(body),
        },
    )


def test_webhook_persists_inbound_event_before_ack_without_processing(
    webhook_client,
):
    client, db = webhook_client

    response = send_webhook(client)

    assert response.status_code == 200
    assert response.json() == {"status": "received", "accepted_events": 1}
    event = db.scalar(select(InboundWhatsAppEvent))
    assert event is not None
    assert event.whatsapp_message_id == "wamid.test_webhook_message"
    assert event.event_payload["text_content"] == "Water leakage problem."
    assert event.status.value == "PENDING"
    assert db.scalar(select(Complaint)) is None
    assert db.scalar(select(ComplaintMessage)) is None
    assert db.scalar(select(OutboundWhatsAppMessage)) is None


def test_duplicate_webhook_is_acknowledged_without_duplicate_inbox_job(
    webhook_client,
):
    client, db = webhook_client

    first_response = send_webhook(client)
    duplicate_response = send_webhook(client)

    assert first_response.status_code == duplicate_response.status_code == 200
    assert first_response.json()["accepted_events"] == 1
    assert duplicate_response.json()["accepted_events"] == 0
    assert (
        db.scalar(
            select(InboundWhatsAppEvent).where(
                InboundWhatsAppEvent.whatsapp_message_id == "wamid.test_webhook_message"
            )
        )
        is not None
    )
    assert len(list(db.scalars(select(InboundWhatsAppEvent)))) == 1


def test_webhook_persists_status_callbacks_for_worker_processing(webhook_client):
    client, db = webhook_client
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
    assert response.json() == {"status": "received", "accepted_events": 1}
    event = db.scalar(select(InboundWhatsAppEvent))
    assert event.event_type.value == "STATUS_CALLBACK"
    assert event.event_payload["status"] == "delivered"
    assert event.status.value == "PENDING"


def test_invalid_signature_is_rejected_without_persisting_events(webhook_client):
    client, db = webhook_client

    response = send_webhook(client, signature="sha256=invalid")

    assert response.status_code == 401
    assert list(db.scalars(select(InboundWhatsAppEvent))) == []


def test_verification_challenge_behavior_is_preserved(webhook_client):
    client, _ = webhook_client

    response = client.get(
        "/api/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": WHATSAPP_VERIFY_TOKEN,
            "hub.challenge": "challenge-value",
        },
    )

    assert response.status_code == 200
    assert response.text == "challenge-value"
