
from decimal import Decimal

from app.models.complaint_message import (
    ComplaintMessage,
    MessageType,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


def save_complaint_message(
    db: Session,
    complaint_id,
    whatsapp_message_id: str,
    message_type: MessageType,
    text_content: str | None = None,
    media_id: str | None = None,
    media_mime_type: str | None = None,
    media_storage_key: str | None = None,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
) -> ComplaintMessage:
    """Save an incoming WhatsApp message against a complaint."""

    statement = (
        insert(ComplaintMessage)
        .values(
            complaint_id=complaint_id,
            whatsapp_message_id=whatsapp_message_id,
            message_type=message_type,
            text_content=text_content,
            media_id=media_id,
            media_mime_type=media_mime_type,
            media_storage_key=media_storage_key,
            latitude=latitude,
            longitude=longitude,
        )
        .on_conflict_do_nothing(
            index_elements=[ComplaintMessage.whatsapp_message_id]
        )
        .returning(ComplaintMessage)
    )

    message = db.scalars(statement).one_or_none()

    if message is not None:
        return message

    # This WhatsApp message was already processed.
    existing_message = db.scalar(
        select(ComplaintMessage).where(
            ComplaintMessage.whatsapp_message_id
            == whatsapp_message_id
        )
    )

    if existing_message is None:
        raise RuntimeError("Unable to save or retrieve complaint message")

    # Never silently associate a duplicate WhatsApp message
    # with a different complaint.
    if existing_message.complaint_id != complaint_id:
        raise ValueError(
            "WhatsApp message is already associated with another complaint"
        )

    return existing_message
