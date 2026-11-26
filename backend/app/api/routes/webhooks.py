import hashlib
import hmac
import json
import secrets

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.services.complaint_processing_service import (
    process_incoming_message,
)
from app.services.whatsapp_media_service import WhatsAppMediaDownloadError
from app.services.whatsapp_outbox_service import (
    dispatch_outbound_message,
)
from app.services.whatsapp_payload_service import (
    ParsedWebhookPayload,
    PayloadValidationError,
    parse_webhook_payload,
)

router = APIRouter()


@router.get("/whatsapp", response_class=PlainTextResponse)
def verify_whatsapp_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
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
    db: Session = Depends(get_db),  # noqa: B008
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
    expected_signature = (
        "sha256="
        + hmac.new(
            settings.meta_app_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
    )

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

        # Normalize the verified Meta payload.
    try:
        parsed_payload = parse_webhook_payload(payload)
    except PayloadValidationError as err:
        raise HTTPException(
            status_code=400,
            detail=str(err),
        ) from err

    # Process incoming messages.
    # Status events are acknowledged but do not create complaints.
    processed_messages = 0

    for message in parsed_payload.messages:
        try:
            result = await process_incoming_message(
                db=db,
                message=message,
            )
        except WhatsAppMediaDownloadError as err:
            raise HTTPException(
                status_code=503,
                detail="Incoming WhatsApp media could not be downloaded",
            ) from err
        processed_messages += 1

        # Find the outbox row associated with this inbound message.
        # This also works for duplicate webhook deliveries.
        outbound_message = db.scalar(
            select(OutboundWhatsAppMessage).where(
                OutboundWhatsAppMessage.inbound_message_id == result.message.id
            )
        )

        if outbound_message is None:
            continue

        if outbound_message.status not in {
            OutboundMessageStatus.PENDING,
            OutboundMessageStatus.FAILED,
        }:
            continue

        # Complaint processing has already committed.
        # Sending failure must not undo complaint registration.
        await dispatch_outbound_message(
            db=db,
            outbound_message_id=outbound_message.id,
        )

    # Acknowledge receipt to Meta.
    return {
        "status": "received",
        "processed_messages": processed_messages,
    }


# ==============================================================================
# Development / Swagger Testing Endpoints
# ==============================================================================


@router.post(
    "/whatsapp/test-parser",
    response_model=ParsedWebhookPayload,
    summary="[TEST] Parse raw WhatsApp payload",
    description="Test endpoint for Swagger: parses any WhatsApp webhook payload using the Pydantic parser and returns normalized messages and status receipts.",
)
def test_parse_whatsapp_payload(
    payload: dict = Body(  # noqa: B008
        ...,
        openapi_examples={
            "Text Message": {
                "summary": "Sample Text Message",
                "value": {
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
                                                "id": "wamid.text12345",
                                                "timestamp": "1732345678",
                                                "type": "text",
                                                "text": {
                                                    "body": "There is a water leakage near my house."
                                                },
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                },
            },
            "Image Message": {
                "summary": "Sample Image Message with Caption",
                "value": {
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
                                                "id": "wamid.img12345",
                                                "timestamp": "1732345679",
                                                "type": "image",
                                                "image": {
                                                    "id": "media_img_001",
                                                    "mime_type": "image/jpeg",
                                                    "caption": "Broken pipeline on street",
                                                },
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                },
            },
            "Location Message": {
                "summary": "Sample Location Message",
                "value": {
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
                                                "id": "wamid.loc12345",
                                                "timestamp": "1732345680",
                                                "type": "location",
                                                "location": {
                                                    "latitude": 12.9716,
                                                    "longitude": 77.5946,
                                                    "name": "Town Hall",
                                                    "address": "MG Road, Bengaluru",
                                                },
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                },
            },
            "Status Delivery Receipt": {
                "summary": "Sample Delivery Status Event",
                "value": {
                    "object": "whatsapp_business_account",
                    "entry": [
                        {
                            "id": "100200300",
                            "changes": [
                                {
                                    "field": "messages",
                                    "value": {
                                        "messaging_product": "whatsapp",
                                        "statuses": [
                                            {
                                                "id": "wamid.sent001",
                                                "status": "delivered",
                                                "timestamp": "1732345690",
                                                "recipient_id": "919876543210",
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                },
            },
        },
    ),
):
    """Parse incoming Meta WhatsApp payload without requiring Meta HMAC signature."""
    try:
        return parse_webhook_payload(payload)
    except PayloadValidationError as err:
        raise HTTPException(status_code=400, detail=str(err))


@router.get(
    "/whatsapp/sample-payload/{message_type}",
    summary="[TEST] Get sample WhatsApp webhook payloads",
    description="Returns pre-built Meta webhook JSON payloads for 'text', 'image', 'audio', 'location', or 'status'.",
)
def get_sample_payload(message_type: str):
    """Retrieve sample payload JSON for testing."""
    sample_type = message_type.lower()
    samples = {
        "text": {
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
                                        "id": "wamid.sample_text",
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
        },
        "image": {
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
                                        "id": "wamid.sample_image",
                                        "timestamp": "1732345679",
                                        "type": "image",
                                        "image": {
                                            "id": "media-id-img-001",
                                            "mime_type": "image/jpeg",
                                            "caption": "Broken pipeline photo",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        },
        "audio": {
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
                                        "id": "wamid.sample_audio",
                                        "timestamp": "1732345680",
                                        "type": "audio",
                                        "audio": {
                                            "id": "media-id-audio-001",
                                            "mime_type": "audio/ogg; codecs=opus",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        },
        "location": {
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
                                        "id": "wamid.sample_location",
                                        "timestamp": "1732345681",
                                        "type": "location",
                                        "location": {
                                            "latitude": 12.9716,
                                            "longitude": 77.5946,
                                            "name": "Town Hall",
                                            "address": "MG Road, Bengaluru",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        },
        "status": {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "100200300",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "statuses": [
                                    {
                                        "id": "wamid.sent001",
                                        "status": "delivered",
                                        "timestamp": "1732345690",
                                        "recipient_id": "919876543210",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        },
    }

    if sample_type not in samples:
        raise HTTPException(
            status_code=404,
            detail=f"Sample for '{sample_type}' not found. Supported: {list(samples.keys())}",
        )

    return samples[sample_type]
