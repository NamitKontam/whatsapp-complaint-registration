
from app.db.session import SessionLocal
from app.services.user_service import get_or_create_user


def main():
    with SessionLocal() as db:
        try:
            first_user = get_or_create_user(
                db,
                "919876543210",
            )

            db.commit()

            first_id = first_user.id

            print("First request:")
            print("User ID:", first_id)
            print("WhatsApp:", first_user.whatsapp_number)

            second_user = get_or_create_user(
                db,
                "+91 98765-43210",
            )

            print("\nSecond request:")
            print("User ID:", second_user.id)
            print("WhatsApp:", second_user.whatsapp_number)

            print("\nSame user:", first_id == second_user.id)

            third_user = get_or_create_user(
                db,
                "919876543211",
            )

            db.commit()

            print("\nThird request:")
            print("User ID:", third_user.id)
            print("WhatsApp:", third_user.whatsapp_number)
            print("Different user:", first_id != third_user.id)

        except Exception:
            db.rollback()
            raise


if __name__ == "__main__":
    main()
