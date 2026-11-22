
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings


@pytest.fixture(scope="session")
def test_engine():
    """Create an engine connected exclusively to the test database."""

    settings = get_settings()

    if not settings.test_database_url:
        raise RuntimeError("TEST_DATABASE_URL is not configured")

    engine = create_engine(
        settings.test_database_url,
        pool_pre_ping=True,
    )

    # Safety check: never run automated tests against the development DB.
    with engine.connect() as connection:
        database_name = connection.scalar(
            text("SELECT current_database()")
        )

    if database_name != "complaints_test_db":
        engine.dispose()
        raise RuntimeError(
            f"Expected complaints_test_db, connected to {database_name}"
        )

    yield engine

    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """Provide a test session whose changes are rolled back afterward."""

    connection = test_engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
