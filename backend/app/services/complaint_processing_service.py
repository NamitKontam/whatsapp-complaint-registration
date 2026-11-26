import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.complaint import Complaint, ComplaintStatus, ConversationState
from app.models.complaint_message import ComplaintMessage, MessageType
from app.models.outbound_whatsapp_message import (
    OutboundMessageStatus,
    OutboundWhatsAppMessage,
)
from app.models.user import User
from app.schemas.whatsapp_message import (
    IncomingWhatsAppMessage,
    MessageKind,
)
from app.services.complaint_message_service import save_complaint_message
from app.services.complaint_service import (
    get_or_create_active_complaint,
    submit_complaint,
)
from app.services.conversation_service import (
    ConversationEvent,
    classify_interactive_reply,
    classify_reply,
    get_next_conversation_state,
    is_greeting_reply,
)
from app.services.user_service import get_or_create_user
from app.services.whatsapp_media_service import (
    WhatsAppMediaDownload,
    WhatsAppMediaDownloadError,
    download_whatsapp_media,
    remove_stored_whatsapp_media,
)


@dataclass(frozen=True)
class ComplaintProcessingResult:
    user: User
    complaint: Complaint
    message: ComplaintMessage
    event: ConversationEvent | None
    is_duplicate: bool
    submitted: bool
    needs_more_information: bool
    is_greeting: bool = False


def _map_message_type(message_type: MessageKind) -> MessageType:
    mapping = {
        MessageKind.TEXT: MessageType.TEXT,
        MessageKind.IMAGE: MessageType.IMAGE,
        MessageKind.AUDIO: MessageType.AUDIO,
        MessageKind.LOCATION: MessageType.LOCATION,
        MessageKind.INTERACTIVE: MessageType.OTHER,
        MessageKind.UNSUPPORTED: MessageType.OTHER,
    }
    return mapping[message_type]


def _get_locked_user(
    db: Session,
    whatsapp_number: str,
) -> User:
    user = get_or_create_user(db, whatsapp_number)

    locked_user = db.scalar(select(User).where(User.id == user.id).with_for_update())

    if locked_user is None:
        raise RuntimeError("Unable to retrieve and lock user")

    return locked_user


def _get_existing_message(
    db: Session,
    whatsapp_message_id: str,
) -> ComplaintMessage | None:
    return db.scalar(
        select(ComplaintMessage).where(
            ComplaintMessage.whatsapp_message_id == whatsapp_message_id
        )
    )


def _validate_duplicate_message_owner(
    db: Session,
    existing_message: ComplaintMessage,
    user_id: uuid.UUID,
) -> None:
    complaint = db.get(Complaint, existing_message.complaint_id)

    if complaint is None:
        raise RuntimeError("Existing message references a missing complaint")

    if complaint.user_id != user_id:
        raise ValueError("WhatsApp message is already associated with another user")


def _classify_message_event(
    message: IncomingWhatsAppMessage,
) -> ConversationEvent:
    if message.message_type == MessageKind.TEXT:
        return classify_reply(message.text_content or "")

    if (
        message.message_type == MessageKind.INTERACTIVE
        and message.interactive_reply is not None
    ):
        return classify_interactive_reply(message.interactive_reply)

    return ConversationEvent.MESSAGE_RECEIVED


def _save_incoming_message(
    db: Session,
    complaint: Complaint,
    message: IncomingWhatsAppMessage,
    is_greeting: bool = False,
    media_storage_key: str | None = None,
) -> tuple[ComplaintMessage, bool]:
    interactive_reply = message.interactive_reply

    return save_complaint_message(
        db=db,
        complaint_id=complaint.id,
        whatsapp_message_id=message.whatsapp_message_id,
        message_type=_map_message_type(message.message_type),
        text_content=(None if is_greeting else message.text_content),
        media_id=message.media_id,
        media_mime_type=message.media_mime_type,
        media_storage_key=media_storage_key,
        caption=message.caption,
        latitude=message.latitude,
        longitude=message.longitude,
        location_name=message.location_name,
        location_address=message.location_address,
        interactive_reply_type=(
            interactive_reply.reply_type.value
            if interactive_reply is not None
            else None
        ),
        interactive_reply_id=(
            interactive_reply.reply_id if interactive_reply is not None else None
        ),
        interactive_reply_title=(
            interactive_reply.title if interactive_reply is not None else None
        ),
        raw_type=message.raw_type,
    )


def _build_duplicate_result(
    db: Session,
    existing_message: ComplaintMessage,
    user: User,
) -> ComplaintProcessingResult:
    complaint = db.get(Complaint, existing_message.complaint_id)

    if complaint is None:
        raise RuntimeError("Existing message references a missing complaint")

    if complaint.user_id != user.id:
        raise ValueError("WhatsApp message is already associated with another user")

    return ComplaintProcessingResult(
        user=user,
        complaint=complaint,
        message=existing_message,
        event=None,
        is_duplicate=True,
        submitted=complaint.status == ComplaintStatus.SUBMITTED,
        needs_more_information=False,
    )


def _get_active_complaint(
    db: Session,
    user: User,
    now: datetime,
) -> Complaint:
    return get_or_create_active_complaint(db, user.id, now=now)


def _is_meaningful_message(
    message: IncomingWhatsAppMessage,
    is_greeting: bool,
) -> bool:
    if is_greeting:
        return False

    if message.message_type == MessageKind.TEXT:
        return bool((message.text_content or "").strip()) and (
            classify_reply(message.text_content or "")
            == ConversationEvent.MESSAGE_RECEIVED
        )

    if message.message_type in {MessageKind.IMAGE, MessageKind.AUDIO}:
        return message.media_id is not None

    if message.message_type == MessageKind.LOCATION:
        return message.latitude is not None and message.longitude is not None

    return False


def _update_conversation_state(
    complaint: Complaint,
    event: ConversationEvent,
) -> None:
    complaint.conversation_state = get_next_conversation_state(
        complaint.conversation_state,
        event,
    )


def _try_submit_complaint(
    db: Session,
    complaint: Complaint,
) -> bool:
    try:
        submit_complaint(db, complaint)
    except ValueError as exc:
        if str(exc) == ("Cannot submit a complaint without meaningful information"):
            complaint.conversation_state = ConversationState.ASKING_FOR_MORE_INFO
            return False

        raise

    return True


def _enqueue_outbound_reply(
    db: Session,
    result: ComplaintProcessingResult,
    recipient: str,
) -> None:
    # Import locally to avoid a circular import:
    # whatsapp_reply_service imports ComplaintProcessingResult.
    from app.services.whatsapp_reply_service import (
        InteractiveWhatsAppReply,
        get_whatsapp_reply,
    )

    reply = get_whatsapp_reply(result)

    # Duplicate messages and other no-reply cases are not queued.
    if reply is None:
        return

    if isinstance(reply, InteractiveWhatsAppReply):
        body = reply.body
        interactive_buttons = [dict(button) for button in reply.buttons]
    else:
        body = reply
        interactive_buttons = None

    outbound_message = OutboundWhatsAppMessage(
        inbound_message_id=result.message.id,
        complaint_id=result.complaint.id,
        recipient=recipient,
        body=body,
        interactive_buttons=interactive_buttons,
        status=OutboundMessageStatus.PENDING,
    )

    db.add(outbound_message)


async def process_incoming_message(
    db: Session,
    message: IncomingWhatsAppMessage,
    *,
    now: datetime | None = None,
) -> ComplaintProcessingResult:
    downloaded_media: WhatsAppMediaDownload | None = None
    processing_time = now or datetime.now(UTC)
    if processing_time.tzinfo is None or processing_time.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    processing_time = processing_time.astimezone(UTC)

    try:
        # 1. Get or create the user and lock their row.
        user = _get_locked_user(
            db=db,
            whatsapp_number=message.whatsapp_number,
        )

        # 2. Check whether this WhatsApp message was already processed.
        existing_message = _get_existing_message(
            db=db,
            whatsapp_message_id=message.whatsapp_message_id,
        )

        # 3. If duplicate, return the existing result.
        if existing_message is not None:
            result = _build_duplicate_result(
                db=db,
                existing_message=existing_message,
                user=user,
            )

            # Commit releases the database row lock.
            db.commit()

            return result

        # 4. Get or create the user's active complaint.
        complaint = _get_active_complaint(
            db=db,
            user=user,
            now=processing_time,
        )

        # 5. Download media only after the duplicate check, while the
        # user's row lock prevents duplicate deliveries from downloading twice.
        if message.message_type in {MessageKind.IMAGE, MessageKind.AUDIO}:
            if message.media_id is None:
                raise ValueError("Media messages require a WhatsApp media ID")

            downloaded_media = await download_whatsapp_media(
                message.media_id,
                expected_mime_type=message.media_mime_type,
            )

        # 6. Classify the incoming message.
        event = _classify_message_event(message)
        is_greeting = message.message_type == MessageKind.TEXT and is_greeting_reply(
            message.text_content or ""
        )
        is_meaningful = _is_meaningful_message(message, is_greeting)

        # 7. Save the incoming message and its metadata.
        saved_message, is_new = _save_incoming_message(
            db=db,
            complaint=complaint,
            message=message,
            is_greeting=is_greeting,
            media_storage_key=(
                downloaded_media.storage_key if downloaded_media is not None else None
            ),
        )

        # 8. Handle a duplicate detected during message insertion.
        if not is_new:
            if downloaded_media is not None:
                remove_stored_whatsapp_media(downloaded_media.storage_key)
                downloaded_media = None

            _validate_duplicate_message_owner(
                db=db,
                existing_message=saved_message,
                user_id=user.id,
            )

            result = _build_duplicate_result(
                db=db,
                existing_message=saved_message,
                user=user,
            )

            db.commit()
            return result

        if is_meaningful:
            complaint.last_activity_at = processing_time

        # 9. Handle submission before changing the state to COMPLETED.
        submitted = False
        needs_more_information = False

        if not is_greeting:
            if event == ConversationEvent.NO:
                submitted = _try_submit_complaint(
                    db=db,
                    complaint=complaint,
                )

                needs_more_information = not submitted
            else:
                # Ordinary messages and YES replies update the state here.
                _update_conversation_state(
                    complaint=complaint,
                    event=event,
                )

        # 10. Build the processing result.
        result = ComplaintProcessingResult(
            user=user,
            complaint=complaint,
            message=saved_message,
            event=event,
            is_duplicate=False,
            submitted=submitted,
            needs_more_information=needs_more_information,
            is_greeting=is_greeting,
        )

        # 11. Queue the reply in the same transaction.
        _enqueue_outbound_reply(
            db=db,
            result=result,
            recipient=message.whatsapp_number,
        )

        # 12. Commit the complaint, inbound message, and outbox together.
        db.commit()
        downloaded_media = None

        return result

    except Exception as processing_error:
        db.rollback()
        if downloaded_media is not None:
            try:
                remove_stored_whatsapp_media(downloaded_media.storage_key)
            except WhatsAppMediaDownloadError as cleanup_error:
                raise ExceptionGroup(
                    "Complaint processing and downloaded media cleanup failed",
                    [processing_error, cleanup_error],
                ) from None
        raise
