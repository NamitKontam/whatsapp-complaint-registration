
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.complaint import Complaint, ComplaintStatus, ConversationState
from app.models.complaint_message import ComplaintMessage, MessageType
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
)
from app.services.user_service import get_or_create_user


@dataclass(frozen=True)
class ComplaintProcessingResult:
    user: User
    complaint: Complaint
    message: ComplaintMessage
    event: ConversationEvent | None
    is_duplicate: bool
    submitted: bool
    needs_more_information: bool


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

    locked_user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
    )

    if locked_user is None:
        raise RuntimeError("Unable to retrieve and lock user")

    return locked_user

def _get_existing_message(
    db: Session,
    whatsapp_message_id: str,
) -> ComplaintMessage | None:
    return db.scalar(
        select(ComplaintMessage).where(
            ComplaintMessage.whatsapp_message_id
            == whatsapp_message_id
        )
    )

def _validate_duplicate_message_owner(
    db: Session,
    existing_message: ComplaintMessage,
    user_id: uuid.UUID,
) -> None:
    complaint = db.get(Complaint, existing_message.complaint_id)

    if complaint is None:
        raise RuntimeError(
            "Existing message references a missing complaint"
        )

    if complaint.user_id != user_id:
        raise ValueError(
            "WhatsApp message is already associated with another user"
        )
    
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
) -> tuple[ComplaintMessage, bool]:
    interactive_reply = message.interactive_reply

    return save_complaint_message(
        db=db,
        complaint_id=complaint.id,
        whatsapp_message_id=message.whatsapp_message_id,
        message_type=_map_message_type(message.message_type),
        text_content=message.text_content,
        media_id=message.media_id,
        media_mime_type=message.media_mime_type,
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
            interactive_reply.reply_id
            if interactive_reply is not None
            else None
        ),
        interactive_reply_title=(
            interactive_reply.title
            if interactive_reply is not None
            else None
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
        raise RuntimeError(
            "Existing message references a missing complaint"
        )

    if complaint.user_id != user.id:
        raise ValueError(
            "WhatsApp message is already associated with another user"
        )

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
) -> Complaint:
    return get_or_create_active_complaint(db, user.id)

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
        if str(exc) == (
            "Cannot submit a complaint without meaningful information"
        ):
            complaint.conversation_state = (
                ConversationState.ASKING_FOR_MORE_INFO
            )
            return False

        raise

    return True