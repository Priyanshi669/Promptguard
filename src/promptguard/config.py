"""Application configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    nvidia_api_key: str = Field(..., min_length=1)

    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        min_length=1,
    )

    nvidia_model: str = Field(..., min_length=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()  # type: ignore[call-arg]