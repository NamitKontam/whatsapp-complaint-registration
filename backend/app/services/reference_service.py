from sqlalchemy import text
from sqlalchemy.orm import Session


def generate_complaint_number(db: Session) -> str:
    sequence_value = db.scalar(
        text("SELECT nextval('complaint_number_seq')")
    )

    if sequence_value is None:
        raise RuntimeError("Unable to generate complaint number")

    return f"CMP-{sequence_value:06d}"