
from app.db.session import SessionLocal
from app.models.complaint_message import ComplaintMessage, MessageType
from app.services.complaint_message_service import save_complaint_message
from app.services.complaint_service import get_or_create_active_complaint
from app.services.user_service import get_or_create_user
from sqlalchemy import select


def main():
    with SessionLocal() as db:
        try:
            user = get_or_create_user(
                db,
                "919876543210",
            )

            complaint = get_or_create_active_complaint(
                db,
                user.id,
            )

            db.commit()

            # First incoming WhatsApp message.
            first_message = save_complaint_message(
                db=db,
                complaint_id=complaint.id,
                whatsapp_message_id="wamid.test001",
                message_type=MessageType.TEXT,
                text_content="There is water leakage near my house.",
            )

            db.commit()

            first_message_id = first_message.id

            print("First message:")
            print("Message ID:", first_message_id)
            print("Content:", first_message.text_content)
            print("Type:", first_message.message_type.value)

            # Simulate receiving the same webhook again.
            duplicate_message = save_complaint_message(
                db=db,
                complaint_id=complaint.id,
                whatsapp_message_id="wamid.test001",
                message_type=MessageType.TEXT,
                text_content="There is water leakage near my house.",
            )

            print("\nDuplicate webhook:")
            print("Message ID:", duplicate_message.id)

            print(
                "Same message:",
                first_message_id == duplicate_message.id,
            )

            # Save a second, distinct message.
            second_message = save_complaint_message(
                db=db,
                complaint_id=complaint.id,
                whatsapp_message_id="wamid.test002",
                message_type=MessageType.TEXT,
                text_content="It has been happening since yesterday.",
            )

            db.commit()

            print("\nSecond message:")
            print("Message ID:", second_message.id)
            print("Content:", second_message.text_content)

            saved_messages = db.scalars(
                select(ComplaintMessage).where(
                    ComplaintMessage.complaint_id == complaint.id
                )
            ).all()

            print("\nMessages belonging to this complaint:")

            for message in saved_messages:
                print(
                    message.whatsapp_message_id,
                    "->",
                    message.text_content,
                )

            print("Total messages:", len(saved_messages))

        except Exception:
            db.rollback()
            raise


if __name__ == "__main__":
    main()
