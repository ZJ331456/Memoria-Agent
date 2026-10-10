from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    pass


class ContextLengthError(ProviderError):
    pass


class ContentSafetyError(ProviderError):
    pass


@dataclass(slots=True)
class ChatResult:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    retries: int = 0
    finish_reason: str | None = None
