import pytest

from app.models.user import User
from app.services.user_service import (
    get_or_create_user,
    normalize_whatsapp_number,
)


def test_create_new_user(db_session):
    user = get_or_create_user(
        db_session,
        "919876543210",
    )

    assert user.id is not None
    assert user.whatsapp_number == "+919876543210"


def test_existing_user_is_retrieved(db_session):
    first_user = get_or_create_user(
        db_session,
        "919876543210",
    )

    second_user = get_or_create_user(
        db_session,
        "919876543210",
    )

    assert first_user.id == second_user.id


def test_different_users_are_created(db_session):
    first_user = get_or_create_user(
        db_session,
        "919876543210",
    )

    second_user = get_or_create_user(
        db_session,
        "919876543211",
    )

    assert first_user.id != second_user.id


@pytest.mark.parametrize(
    ("phone_number", "expected"),
    [
        ("919876543210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
    ],
)
def test_normalize_whatsapp_number(phone_number, expected):
    assert normalize_whatsapp_number(phone_number) == expected


@pytest.mark.parametrize(
    "phone_number",
    [
        "",
        "123",
        "abcdefghijk",
        "1234567890123456",
    ],
)
def test_invalid_whatsapp_number(phone_number):
    with pytest.raises(ValueError):
        normalize_whatsapp_number(phone_number)


def test_user_has_no_name_by_default(db_session):
    user = get_or_create_user(
        db_session,
        "919876543210",
    )

    assert user.name is None
