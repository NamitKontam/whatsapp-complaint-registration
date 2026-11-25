
import httpx

from app.core.config import get_settings


async def send_text_message(
    recipient: str,
    message: str,
) -> dict:
    settings = get_settings()

    if not all(
        [
            settings.whatsapp_access_token,
            settings.whatsapp_phone_number_id,
            settings.whatsapp_api_version,
        ]
    ):
        raise ValueError("WhatsApp API configuration is incomplete")

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
            "body": message,
        },
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

    response.raise_for_status()

    return response.json()
