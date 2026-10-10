"""In-process transformers backend via local://cuda|cpu|auto."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ...config import ModelConfig
from ..types import ChatResult, ProviderError
from .process_engine import get_local_llm, model_path


class ProcessLocalBackend:
    name = "process_local"

    def __init__(self, *, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds

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
        del stream  # transformers path generates fully; stream emits once via on_delta
        device = selected.base_url.removeprefix("local://").rstrip("/")
        if device not in {"cuda", "cpu", "auto"}:
            raise ProviderError("本地 LLM 地址应为 local://cuda、local://cpu 或 local://auto")
        started = time.perf_counter()
        try:
            engine = get_local_llm(str(model_path(selected.model)), device)
            data = await engine.chat(
                messages,
                tools,
                max_tokens,
                max(self.timeout_seconds, 120),
            )
        except asyncio.TimeoutError as exc:
            raise ProviderError("本地 LLM 推理超时") from exc
        except Exception as exc:
            raise ProviderError(f"本地 LLM 推理失败: {type(exc).__name__}: {exc}") from exc
        content = str(data.get("content") or "")
        if on_delta and content:
            await on_delta(content)
        return ChatResult(
            content,
            list(data.get("tool_calls") or []),
            dict(data.get("raw_message") or {}),
            {key: int(value) for key, value in (data.get("usage") or {}).items() if isinstance(value, int)},
            int((time.perf_counter() - started) * 1000),
            0,
            data.get("finish_reason"),
        )
