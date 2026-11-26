
import httpx

from app.core.config import get_settings


class WhatsAppSendError(Exception):
    """Raised when a WhatsApp Cloud API message cannot be sent."""


async def _send_whatsapp_payload(
    recipient: str,
    payload: dict,
) -> dict:
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


async def send_whatsapp_text_message(
    recipient: str,
    text: str,
) -> dict:
    """Send a plain text WhatsApp message through Meta Cloud API."""

    return await _send_whatsapp_payload(
        recipient,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text,
            },
        },
    )


async def send_whatsapp_interactive_message(
    recipient: str,
    text: str,
    buttons: list[dict[str, str]],
) -> dict:
    """Send a reply-button WhatsApp message through Meta Cloud API."""

    if not 1 <= len(buttons) <= 3:
        raise WhatsAppSendError(
            "Interactive WhatsApp messages require one to three buttons."
        )

    payload_buttons = []
    for button in buttons:
        button_id = button.get("id", "")
        title = button.get("title", "")
        if not button_id or len(button_id) > 256:
            raise WhatsAppSendError(
                "Interactive WhatsApp button IDs must be 1-256 characters."
            )
        if not title or len(title) > 20:
            raise WhatsAppSendError(
                "Interactive WhatsApp button titles must be 1-20 characters."
            )

        payload_buttons.append(
            {
                "type": "reply",
                "reply": {
                    "id": button_id,
                    "title": title,
                },
            }
        )

    return await _send_whatsapp_payload(
        recipient,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {"buttons": payload_buttons},
            },
        },
    )
