"""Shared constrained scalar types for domain models."""

from typing import Annotated

from pydantic import StringConstraints

NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
