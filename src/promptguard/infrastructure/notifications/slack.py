"""Notification implementations.

This module is the only place that talks to the Slack webhook. Every
failure is raised as :class:`SlackDeliveryError` with a sanitized
message that never contains the webhook URL, tokens, or payload text.
"""

import asyncio
import json
import urllib.error
import urllib.request

from promptguard.application.contracts import Notifier


class SlackDeliveryError(RuntimeError):
    """Sanitized Slack delivery failure raised after bounded attempts."""


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class NoopNotifier(Notifier):
    """Safe default notifier that intentionally does nothing."""

    async def notify(self, message: str) -> None:
        """Accept a notification without requiring Slack configuration."""


class SlackNotifier(Notifier):
    """Deliver notifications to a Slack incoming webhook."""

    def __init__(
        self,
        webhook_url: str,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
    ) -> None:
        """Initialize the notifier with bounded attempts and a timeout."""
        self._webhook_url = webhook_url
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max(1, int(max_attempts))

    async def notify(self, message: str) -> None:
        """POST the message to the configured Slack webhook.

        Transient failures (network errors, timeouts, 429/5xx) are
        retried with a bounded backoff; permanent rejections (other 4xx)
        fail immediately. The webhook URL never appears in a raised
        error message.
        """
        payload = json.dumps({"text": message}).encode("utf-8")
        last_failure: str = "unknown failure"
        for attempt in range(1, self._max_attempts + 1):
            try:
                await asyncio.to_thread(
                    _post, self._webhook_url, payload, self._timeout_seconds,
                )
                return
            except urllib.error.HTTPError as exc:
                exc.close()
                status = exc.code
                if status not in RETRYABLE_STATUS_CODES:
                    raise SlackDeliveryError(
                        f"Slack delivery rejected by the webhook "
                        f"(status={status})."
                    ) from exc
                last_failure = f"HTTP {status}"
            except Exception as exc:  # noqa: BLE001 - sanitized re-raise
                last_failure = type(exc).__name__
            if attempt < self._max_attempts:
                await asyncio.sleep(min(0.25 * 2 ** (attempt - 1), 2.0))
        raise SlackDeliveryError(
            f"Slack delivery failed after {self._max_attempts} attempts "
            f"(last failure: {last_failure})."
        )


def _post(url: str, payload: bytes, timeout_seconds: float) -> None:
    """Perform the blocking webhook request outside the event loop."""
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response.read()
