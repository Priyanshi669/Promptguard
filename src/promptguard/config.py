"""Application configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from promptguard.domain.statistics import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_CONFIDENCE_LEVEL,
    DEFAULT_RESAMPLES,
)
from promptguard.infrastructure.llm.pool import ProviderSpec


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    nvidia_api_key: str = Field(..., min_length=1)

    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        min_length=1,
    )

    nvidia_model: str = Field(..., min_length=1)

    nvidia_request_timeout_seconds: float = Field(default=30.0, gt=0, le=60)

    nvidia_min_request_interval_seconds: float = Field(
        default=1.0, ge=0, le=60,
    )

    provider_pool: list[ProviderSpec] | None = Field(default=None)

    evaluation_timeout_seconds: float = Field(default=900.0, gt=0, le=1800)

    bootstrap_seed: int = Field(default=DEFAULT_BOOTSTRAP_SEED)

    bootstrap_resamples: int = Field(
        default=DEFAULT_RESAMPLES, ge=1, le=100000,
    )

    bootstrap_confidence_level: float = Field(
        default=DEFAULT_CONFIDENCE_LEVEL, gt=0, lt=1,
    )

    nvidia_temperature: float | None = Field(default=None, ge=0, le=2)

    nvidia_seed: int | None = Field(default=None, ge=0)

    api_token: str | None = Field(default=None, min_length=12)

    api_cors_origins: str = "http://localhost:5173"

    api_max_concurrent_runs: int = Field(default=1, ge=1, le=32)

    slack_webhook_url: str | None = Field(default=None)

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