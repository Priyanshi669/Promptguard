"""Helpers for resolving repository identifiers without path escapes."""

import re
from pathlib import Path

_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def validate_identifier(value: str, *, kind: str) -> str:
    """Return a simple identifier or raise ValueError."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{kind} must be a non-empty identifier.")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(
            f"{kind} must not contain path separators or relative components."
        )
    if _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{kind} must contain only letters, digits, dots, hyphens, "
            "or underscores, and must start with a letter or digit."
        )
    return value


def resolve_identifier_path(
    root: Path,
    identifier: str,
    suffix: str,
    *,
    kind: str,
) -> Path:
    """Resolve an identifier under root, rejecting directory escapes."""
    validate_identifier(identifier, kind=kind)
    resolved_root = root.resolve()
    candidate = (resolved_root / f"{identifier}{suffix}").resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"{kind} must resolve inside {resolved_root}.")
    return candidate
