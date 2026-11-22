
from app.db.session import SessionLocal
from app.services.complaint_service import get_or_create_active_complaint
from app.services.user_service import get_or_create_user


def main():
    with SessionLocal() as db:
        try:
            # Identify the WhatsApp user.
            user = get_or_create_user(
                db,
                "919876543210",
            )

            # First request: create a draft complaint.
            first_complaint = get_or_create_active_complaint(
                db,
                user.id,
            )

            db.commit()

            first_complaint_id = first_complaint.id

            print("First request:")
            print("Complaint ID:", first_complaint_id)
            print("Status:", first_complaint.status.value)
            print("Conversation state:", first_complaint.conversation_state.value)
            print("Complaint number:", first_complaint.complaint_number)

            # Second request: retrieve the existing draft.
            second_complaint = get_or_create_active_complaint(
                db,
                user.id,
            )

            print("\nSecond request:")
            print("Complaint ID:", second_complaint.id)

            print(
                "\nSame complaint:",
                first_complaint_id == second_complaint.id,
            )

        except Exception:
            db.rollback()
            raise


if __name__ == "__main__":
    main()
