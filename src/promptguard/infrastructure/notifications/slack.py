"""Notification implementations."""

import asyncio
import json
import urllib.request

from promptguard.application.contracts import Notifier


class NoopNotifier(Notifier):
    """Safe default notifier that intentionally does nothing."""

    async def notify(self, message: str) -> None:
        """Accept a notification without requiring Slack configuration."""


class SlackNotifier(Notifier):
    """Deliver notifications to a Slack incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        """Initialize the notifier with a Slack webhook URL."""
        self._webhook_url = webhook_url

    async def notify(self, message: str) -> None:
        """POST the message to the configured Slack webhook."""
        payload = json.dumps({"text": message}).encode("utf-8")
        request = urllib.request.Request(
            self._webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        await asyncio.to_thread(_post, request)


def _post(request: urllib.request.Request) -> None:
    """Perform the blocking webhook request off the event loop."""
    urllib.request.urlopen(request)
