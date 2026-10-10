from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ...config import ModelConfig
from ..types import ChatResult


class ChatBackend(Protocol):
    """OpenAI-shaped chat backend used by LLMClient."""

    name: str

    async def chat(
        self,
        selected: ModelConfig,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        stream: bool = False,
    ) -> ChatResult:
        ...
