"""Unit tests for application configuration."""

import pytest
from pydantic import ValidationError

from promptguard.config import Settings


def _settings(**overrides: str) -> Settings:
    """Build settings with the required fields supplied explicitly."""
    fields = {
        "nvidia_api_key": "test-api-key",
        "nvidia_model": "test-model",
    } | overrides
    return Settings(**fields)


def test_request_timeout_defaults_to_thirty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production default must be 30 seconds unless overridden."""
    monkeypatch.delenv("NVIDIA_REQUEST_TIMEOUT_SECONDS", raising=False)
    settings = _settings()
    assert settings.nvidia_request_timeout_seconds == 30.0


def test_request_timeout_env_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An environment override must replace the compiled default."""
    monkeypatch.setenv("NVIDIA_REQUEST_TIMEOUT_SECONDS", "60")
    settings = _settings()
    assert settings.nvidia_request_timeout_seconds == 60.0


@pytest.mark.parametrize("value", ["0", "-1", "61", "120"])
def test_request_timeout_rejects_out_of_range_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Timeouts outside 1-60 seconds must fail validation."""
    monkeypatch.setenv("NVIDIA_REQUEST_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError):
        _settings()
