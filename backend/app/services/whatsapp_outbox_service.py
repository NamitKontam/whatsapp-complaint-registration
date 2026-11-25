
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.services.whatsapp_sender_service import (
    WhatsAppSendError,
    send_whatsapp_text_message,
)


async def dispatch_outbound_message(
    db: Session,
    outbound_message_id: uuid.UUID,
) -> bool:
    """Send one outbox message and persist its delivery result.

    Returns True when the message is sent successfully.
    Returns False when sending fails or the message is not eligible.
    """

    outbound_message = db.get(
        OutboundWhatsAppMessage,
        outbound_message_id,
    )

    if outbound_message is None:
        return False

    if outbound_message.status == OutboundMessageStatus.SENT:
        return True

    if outbound_message.status not in {
        OutboundMessageStatus.PENDING,
        OutboundMessageStatus.FAILED,
    }:
        return False

    # Record the attempt before making the external API call.
    outbound_message.attempts += 1
    outbound_message.last_error = None
    db.commit()

    try:
        response = await send_whatsapp_text_message(
            recipient=outbound_message.recipient,
            text=outbound_message.body,
        )

        messages = response.get("messages", [])
        whatsapp_message_id = (
            messages[0].get("id") if messages else None
        )

        if not whatsapp_message_id:
            raise WhatsAppSendError(
                "WhatsApp API response did not contain a message ID."
            )

    except WhatsAppSendError as exc:
        outbound_message.status = OutboundMessageStatus.FAILED
        outbound_message.last_error = str(exc)
        db.commit()
        return False

    outbound_message.status = OutboundMessageStatus.SENT
    outbound_message.whatsapp_message_id = whatsapp_message_id
    outbound_message.sent_at = datetime.now(UTC)
    outbound_message.last_error = None

    db.commit()
    return True
