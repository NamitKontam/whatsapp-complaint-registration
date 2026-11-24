from app.services.reference_service import generate_complaint_number


def test_generate_complaint_number(db_session):
    first_reference = generate_complaint_number(db_session)
    second_reference = generate_complaint_number(db_session)

    first_number = int(first_reference.removeprefix("CMP-"))
    second_number = int(second_reference.removeprefix("CMP-"))

    assert first_reference == f"CMP-{first_number:06d}"
    assert second_reference == f"CMP-{second_number:06d}"
    assert second_number > first_number