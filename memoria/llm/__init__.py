"""LLM client package: OpenAI-compatible HTTP + optional process-local backend.

Preferred local path on Windows: start ``scripts/serve_local_llm.py`` then point
``[llm.main].base_url`` at ``http://127.0.0.1:8080/v1``. The same HTTP backend
also talks to remote DeepSeek/DashScope and Linux-side vLLM/SGLang/Ollama.
"""

from .backends.openai_http import OpenAICompatBackend, classify_openai_server
from .backends.process import ProcessLocalBackend
from .backends.resolve import backend_public_label, detect_backend_kind, resolve_backend
from .client import LLMClient
from .types import ChatResult, ContentSafetyError, ContextLengthError, ProviderError

__all__ = [
    "ChatResult",
    "ContentSafetyError",
    "ContextLengthError",
    "LLMClient",
    "OpenAICompatBackend",
    "ProcessLocalBackend",
    "ProviderError",
    "backend_public_label",
    "classify_openai_server",
    "detect_backend_kind",
    "resolve_backend",
]
