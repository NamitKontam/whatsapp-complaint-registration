
from sqlalchemy import text


def test_database_fixture_connects_to_test_database(db_session):
    database_name = db_session.scalar(
        text("SELECT current_database()")
    )

    assert database_name == "complaints_test_db"
