"""LLM chat backends: OpenAI-compatible HTTP and optional in-process transformers."""

from .openai_http import OpenAICompatBackend
from .process import ProcessLocalBackend
from .resolve import detect_backend_kind, resolve_backend

__all__ = [
    "OpenAICompatBackend",
    "ProcessLocalBackend",
    "detect_backend_kind",
    "resolve_backend",
]
