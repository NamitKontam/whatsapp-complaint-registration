
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


def normalize_whatsapp_number(whatsapp_number: str) -> str:
    """Convert a WhatsApp number into a consistent international format."""

    digits = "".join(
        character
        for character in whatsapp_number
        if character.isdigit()
    )

    if not 8 <= len(digits) <= 15:
        raise ValueError("Invalid WhatsApp number")

    return f"+{digits}"


def get_or_create_user(
    db: Session,
    whatsapp_number: str,
) -> User:
    """Retrieve an existing WhatsApp user or create a new one."""

    normalized_number = normalize_whatsapp_number(whatsapp_number)

    # Attempt to insert the user.
    # If the number already exists, PostgreSQL does nothing.
    statement = (
        insert(User)
        .values(whatsapp_number=normalized_number)
        .on_conflict_do_nothing(
            index_elements=[User.whatsapp_number]
        )
        .returning(User)
    )

    user = db.scalars(statement).one_or_none()

    if user is not None:
        return user

    # The user already existed, so retrieve that record.
    user = db.scalar(
        select(User).where(
            User.whatsapp_number == normalized_number
        )
    )

    if user is None:
        raise RuntimeError("Unable to retrieve or create WhatsApp user")

    return user
