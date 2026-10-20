"""LLM client contract.

The canonical contract lives in the application layer so application
services never depend on infrastructure. This module re-exports it so
infrastructure providers keep a stable import path.
"""

from promptguard.application.contracts import LLMClient

__all__ = ["LLMClient"]