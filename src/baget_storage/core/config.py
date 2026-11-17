from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAGET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "СкладПро API"
    environment: Literal["local", "test", "production"] = "local"
    database_url: str = Field(
        default="postgresql+asyncpg://baget@localhost:5432/baget_storage", repr=False
    )
    cors_origins: list[str] = Field(default_factory=list)
    access_token_minutes: int = Field(default=60, ge=1, le=1440)
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = "baget-storage"
    jwt_audience: str = "baget-storage-api"
    frontend_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    refresh_token_days: int = Field(default=30, ge=1, le=90)
    email_verification_hours: int = Field(default=24, ge=1, le=72)
    password_reset_minutes: int = Field(default=30, ge=5, le=60)
    reading_opening_day: int = Field(default=1, ge=1, le=28)
    reading_deadline_day: int = Field(default=5, ge=1, le=28)
    smtp_host: str = "mailpit"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_address: str = "noreply@skladpro.local"
    smtp_starttls: bool = False
    smtp_use_tls: bool = False
    smtp_timeout_seconds: int = Field(default=15, ge=1, le=60)
    worker_poll_seconds: int = Field(default=5, ge=1, le=300)
    worker_batch_size: int = Field(default=20, ge=1, le=100)
    worker_max_attempts: int = Field(default=8, ge=1, le=20)

    @model_validator(mode="after")
    def validate_delivery(self) -> Self:
        if self.smtp_starttls and self.smtp_use_tls:
            raise ValueError("Choose either SMTP STARTTLS or implicit TLS")
        if bool(self.smtp_username) != bool(self.smtp_password):
            raise ValueError("SMTP username and password must be set together")
        if self.environment == "production":
            if self.frontend_base_url.scheme != "https":
                raise ValueError("Production frontend_base_url must use HTTPS")
            if not (self.smtp_starttls or self.smtp_use_tls):
                raise ValueError("Production SMTP must use TLS")
        return self

    @field_validator("database_url")
    @classmethod
    def require_async_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use postgresql+asyncpg")
        return value
