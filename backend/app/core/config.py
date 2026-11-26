from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application settings
    app_name: str = "WhatsApp Complaint Registration System"
    app_env: str = "development"

    # Database configuration
    database_url: str | None = None
    migration_database_url: str | None = None

    test_database_url: str | None = None
    test_migration_database_url: str | None = None

    # WhatsApp Cloud API configuration
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_api_version: str | None = None

    # Meta webhook configuration
    meta_app_secret: str | None = None
    whatsapp_verify_token: str | None = None

    # Media storage
    media_storage_dir: Path = Path("storage/media")
    whatsapp_media_max_size_bytes: int = Field(
        default=5_000_000,
        gt=0,
    )

    complaint_draft_expiry_hours: int = Field(
        default=24,
        gt=0,
    )

    whatsapp_outbox_max_attempts: int = Field(
        default=5,
        gt=0,
        le=100,
    )
    whatsapp_outbox_retry_base_seconds: int = Field(
        default=30,
        gt=0,
        le=86400,
    )
    whatsapp_outbox_retry_max_seconds: int = Field(
        default=3600,
        gt=0,
        le=86400,
    )
    whatsapp_outbox_poll_interval_seconds: int = Field(
        default=5,
        gt=0,
        le=3600,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
