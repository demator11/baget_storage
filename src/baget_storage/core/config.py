from typing import Literal

from pydantic import Field, SecretStr, field_validator
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

    @field_validator("database_url")
    @classmethod
    def require_async_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use postgresql+asyncpg")
        return value
