
import hashlib
import hmac
import json
import secrets

from app.core.config import get_settings
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/whatsapp", response_class=PlainTextResponse)
def verify_whatsapp_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(
        default=None, alias="hub.verify_token"
    ),
    hub_challenge: str | None = Query(
        default=None, alias="hub.challenge"
    ),
):
    settings = get_settings()

    if (
        hub_mode == "subscribe"
        and settings.whatsapp_verify_token
        and hub_verify_token
        and secrets.compare_digest(
            hub_verify_token,
            settings.whatsapp_verify_token,
        )
        and hub_challenge is not None
    ):
        return PlainTextResponse(content=hub_challenge)

    raise HTTPException(
        status_code=403,
        detail="Webhook verification failed",
    )


@router.post("/whatsapp")
async def receive_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
):
    settings = get_settings()

    if not settings.meta_app_secret:
        raise HTTPException(
            status_code=500,
            detail="Webhook signature verification is not configured",
        )

    if not x_hub_signature_256:
        raise HTTPException(
            status_code=401,
            detail="Missing webhook signature",
        )

    # Read the original request body before parsing JSON.
    body = await request.body()

    # Calculate the expected Meta signature.
    expected_signature = "sha256=" + hmac.new(
        settings.meta_app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    # Compare signatures safely.
    if not hmac.compare_digest(
        expected_signature,
        x_hub_signature_256,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature",
        )

    # Parse the JSON only after signature verification.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON payload",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook payload",
        )

    # Temporary inspection: identify message types without logging
    # phone numbers or message contents.
    message_types = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for status in value.get("statuses", []):
                print(
                    "WhatsApp message status:",
                    status.get("status"),
                    "Message ID:",
                    status.get("id"),
                    "Recipient:",
                    status.get("recipient_id"),
                    "Errors:",
                    status.get("errors"),
                )

            for message in value.get("messages", []):
                message_types.append(message.get("type", "unknown"))

    print(f"WhatsApp webhook received. Message types: {message_types}")

    # Acknowledge receipt to Meta.
    return {"status": "received"}
