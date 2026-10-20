"""Application configuration, loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_AUTH_PASSWORD_LENGTH = 12

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

    auth_password: str | None = None
    session_secret: str | None = None
    api_token: str | None = None
    secure_cookies: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("database_path")
    @classmethod
    def _resolve_database_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @model_validator(mode="after")
    def _validate_public_deployment(self) -> Settings:
        password = (self.auth_password or "").strip()
        self.auth_password = password or None
        self.session_secret = (self.session_secret or "").strip() or None
        self.api_token = (self.api_token or "").strip() or None

        if self.auth_password and len(self.auth_password) < MIN_AUTH_PASSWORD_LENGTH:
            raise ValueError(
                f"BMS_AUTH_PASSWORD must be at least {MIN_AUTH_PASSWORD_LENGTH} characters"
            )
        if self.is_production:
            if not self.auth_password:
                raise ValueError(
                    "BMS_AUTH_PASSWORD is required when BMS_ENVIRONMENT=production. "
                    "The dashboard is public otherwise."
                )
            if self.telegram_ready and self.telegram_chat_id is None:
                raise ValueError(
                    "BMS_TELEGRAM_CHAT_ID is required in production when Telegram is enabled, "
                    "so strangers cannot control your watches."
                )
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_enabled and self.telegram_bot_token)

    @property
    def effective_check_interval(self) -> int:
        return max(self.default_check_interval_seconds, self.min_check_interval_seconds)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_password)

    @property
    def cookies_secure(self) -> bool:
        if self.secure_cookies is not None:
            return self.secure_cookies
        return self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
