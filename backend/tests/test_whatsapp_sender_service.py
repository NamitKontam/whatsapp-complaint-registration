
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import whatsapp_sender_service as sender


def configure_settings(monkeypatch):
    settings = SimpleNamespace(
        whatsapp_access_token="test-access-token",
        whatsapp_phone_number_id="123456789",
        whatsapp_api_version="vXX.X",
    )

    monkeypatch.setattr(sender, "get_settings", lambda: settings)

    return settings


def mock_http_client(monkeypatch, response):
    mock_client = SimpleNamespace(
        post=AsyncMock(return_value=response),
    )

    class MockAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(
        sender.httpx,
        "AsyncClient",
        MockAsyncClient,
    )

    return mock_client


def test_send_text_message_success(monkeypatch):
    configure_settings(monkeypatch)

    response = httpx.Response(
        200,
        json={
            "messages": [
                {"id": "wamid.test-message-id"},
            ],
        },
        request=httpx.Request(
            "POST",
            "https://graph.facebook.com/",
        ),
    )

    mock_client = mock_http_client(monkeypatch, response)

    result = asyncio.run(
        sender.send_whatsapp_text_message(
            recipient="919876543210",
            text="Your complaint has been received.",
        )
    )

    assert result["messages"][0]["id"] == "wamid.test-message-id"

    mock_client.post.assert_awaited_once()

    call = mock_client.post.call_args

    assert call.args[0] == (
        "https://graph.facebook.com/"
        "vXX.X/123456789/messages"
    )

    assert call.kwargs["headers"]["Authorization"] == (
        "Bearer test-access-token"
    )

    assert call.kwargs["json"]["to"] == "919876543210"
    assert call.kwargs["json"]["type"] == "text"
    assert call.kwargs["json"]["text"]["body"] == (
        "Your complaint has been received."
    )


def test_send_interactive_message_success(monkeypatch):
    configure_settings(monkeypatch)

    response = httpx.Response(
        200,
        json={"messages": [{"id": "wamid.interactive-id"}]},
        request=httpx.Request(
            "POST",
            "https://graph.facebook.com/",
        ),
    )
    mock_client = mock_http_client(monkeypatch, response)
    buttons = [
        {"id": "complaint_add_more", "title": "Yes, add details"},
        {"id": "complaint_submit", "title": "No, submit"},
    ]

    result = asyncio.run(
        sender.send_whatsapp_interactive_message(
            recipient="919876543210",
            text="Would you like to add more details to your complaint?",
            buttons=buttons,
        )
    )

    assert result["messages"][0]["id"] == "wamid.interactive-id"
    call = mock_client.post.call_args
    assert call.args[0] == (
        "https://graph.facebook.com/"
        "vXX.X/123456789/messages"
    )
    assert call.kwargs["headers"]["Authorization"] == (
        "Bearer test-access-token"
    )
    assert call.kwargs["json"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "919876543210",
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "Would you like to add more details to your complaint?"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "complaint_add_more",
                            "title": "Yes, add details",
                        },
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "complaint_submit",
                            "title": "No, submit",
                        },
                    },
                ],
            },
        },
    }


def test_send_text_message_http_error(monkeypatch):
    configure_settings(monkeypatch)

    response = httpx.Response(
        400,
        json={"error": {"message": "Invalid recipient"}},
        request=httpx.Request(
            "POST",
            "https://graph.facebook.com/",
        ),
    )

    mock_http_client(monkeypatch, response)

    with pytest.raises(
        sender.WhatsAppSendError,
        match="HTTP 400",
    ):
        asyncio.run(
            sender.send_whatsapp_text_message(
                recipient="919876543210",
                text="Test message",
            )
        )


def test_send_text_message_missing_token(monkeypatch):
    settings = configure_settings(monkeypatch)
    settings.whatsapp_access_token = None

    with pytest.raises(
        sender.WhatsAppSendError,
        match="access token is not configured",
    ):
        asyncio.run(
            sender.send_whatsapp_text_message(
                recipient="919876543210",
                text="Test message",
            )
        )
