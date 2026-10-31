"""Console entry point serving the PromptGuard API via uvicorn."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def run(argv: Sequence[str] | None = None) -> None:
    """Start the PromptGuard API (equivalent to the docs' uvicorn command)."""
    parser = argparse.ArgumentParser(prog="promptguard-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help=("The port to bind the API to."),
    )
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(
        "promptguard.interfaces.api.app:create_app",
        host=args.host,
        port=args.port,
        factory=True,
    )


__all__ = ["run"]
