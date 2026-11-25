
import httpx

from app.core.config import get_settings


class WhatsAppSendError(Exception):
    """Raised when a WhatsApp Cloud API message cannot be sent."""


async def send_whatsapp_text_message(
    recipient: str,
    text: str,
) -> dict:
    """Send a plain text WhatsApp message through Meta Cloud API."""

    settings = get_settings()

    if not settings.whatsapp_access_token:
        raise WhatsAppSendError("WhatsApp access token is not configured")

    if not settings.whatsapp_phone_number_id:
        raise WhatsAppSendError("WhatsApp phone number ID is not configured")

    if not settings.whatsapp_api_version:
        raise WhatsAppSendError("WhatsApp API version is not configured")

    url = (
        f"https://graph.facebook.com/"
        f"{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )

        response.raise_for_status()

    except httpx.HTTPStatusError as err:
        raise WhatsAppSendError(
            f"WhatsApp API returned HTTP {err.response.status_code}"
        ) from err

    except httpx.RequestError as err:
        raise WhatsAppSendError(
            "Could not connect to WhatsApp Cloud API"
        ) from err

    return response.json()
