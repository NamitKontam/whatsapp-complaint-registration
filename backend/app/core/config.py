
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application settings
    app_name: str = "WhatsApp Complaint Registration System"
    app_env: str = "development"

    # Database configuration
    database_url: str | None = None

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
