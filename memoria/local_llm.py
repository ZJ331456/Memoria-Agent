"""Compatibility shim; prefer ``memoria.llm.backends.process_engine``."""

from .llm.backends.process_engine import (  # noqa: F401
    LocalCausalLM,
    get_local_llm,
    model_path,
    normalize_messages,
    parse_local_completion,
)

__all__ = [
    "LocalCausalLM",
    "get_local_llm",
    "model_path",
    "normalize_messages",
    "parse_local_completion",
]
