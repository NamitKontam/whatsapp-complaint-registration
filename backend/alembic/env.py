
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app import models
from app.core.config import get_settings
from app.db.base import Base

config = context.config

# Configure logging using alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Give Alembic access to our SQLAlchemy model metadata
target_metadata = Base.metadata

# Use the dedicated migration database credentials
# Select the database environment through Alembic's -x argument.
settings = get_settings()

x_args = context.get_x_argument(as_dictionary=True)
environment = x_args.get("environment", "development")

if environment == "test":
    database_url = settings.test_migration_database_url
    missing_setting = "TEST_MIGRATION_DATABASE_URL"

elif environment == "development":
    database_url = settings.migration_database_url
    missing_setting = "MIGRATION_DATABASE_URL"

else:
    raise ValueError(
        f"Unsupported database environment: {environment}"
    )

if not database_url:
    raise RuntimeError(f"{missing_setting} is not configured")


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a live database connection."""

    connectable = create_engine(
        database_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
