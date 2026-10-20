"""Application configuration, loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BMS_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Ticket Watch"
    environment: str = "dev"
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)
    timezone: str = "Asia/Kolkata"
    database_path: Path = PROJECT_ROOT / "data" / "bms_assistant.db"
    log_level: str = "INFO"

    default_check_interval_seconds: int = Field(5, ge=1)
    min_check_interval_seconds: int = Field(2, ge=1)
    max_consecutive_errors: int = Field(5, ge=1)

    telegram_enabled: bool = True
    telegram_bot_token: str | None = None
    telegram_chat_id: int | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("database_path")
    @classmethod
    def _resolve_database_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_enabled and self.telegram_bot_token)

    @property
    def effective_check_interval(self) -> int:
        return max(self.default_check_interval_seconds, self.min_check_interval_seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()
