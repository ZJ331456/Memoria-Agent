"""Unified OpenAI-compatible HTTP backend.

Works for remote APIs (DeepSeek / DashScope) and local servers
(vLLM / SGLang / Ollama / llama.cpp / Memoria serve_local_llm).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ...config import ModelConfig
from ..types import ChatResult, ContentSafetyError, ContextLengthError, ProviderError

logger = logging.getLogger(__name__)
_CONTEXT_HINTS = (
    "context_length_exceeded", "maximum context length", "context window",
    "range of input length", "too many tokens",
)
_SAFETY_HINTS = ("content_filter", "content_policy_violation", "data_inspection_failed")


def classify_openai_server(base_url: str) -> str:
    """Best-effort label for docs/status; routing always uses the same HTTP path."""
    host = (urlparse(base_url).hostname or "").lower()
    path = (urlparse(base_url).path or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost"):
        if ":11434" in base_url:
            return "ollama"
        if "sglang" in path or ":30000" in base_url:
            return "sglang"
        if ":8000" in base_url or "vllm" in path:
            return "vllm-or-openai-local"
        return "openai-local"
    if "deepseek" in host:
        return "deepseek"
    if "dashscope" in host:
        return "dashscope"
    return "openai-compat"


class OpenAICompatBackend:
    name = "openai_http"

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.transport = transport

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
        if stream:
            return await self._chat_stream(selected, messages, max_tokens=max_tokens, tools=tools, on_delta=on_delta)
        payload: dict[str, Any] = {
            "model": selected.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data, metrics = await self._post(selected, selected.auth_headers(), payload)
        message = data["choices"][0]["message"]
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append({"id": call.get("id", ""), "name": fn.get("name", ""), "arguments": arguments})
        usage = {key: int(value) for key, value in (data.get("usage") or {}).items() if isinstance(value, int)}
        return ChatResult(
            str(message.get("content") or message.get("reasoning_content") or ""),
            calls,
            message,
            usage,
            metrics["duration_ms"],
            metrics["retries"],
            data["choices"][0].get("finish_reason"),
        )

    async def _post(
        self,
        selected: ModelConfig,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        last_error: Exception | None = None
        started = time.perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{selected.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    if response.status_code >= 400:
                        self._raise_response_error(response)
                    return response.json(), {
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "retries": attempt,
                    }
                except (ContextLengthError, ContentSafetyError):
                    raise
                except (httpx.TimeoutException, httpx.TransportError, ProviderError) as exc:
                    last_error = exc
                    if not self._retryable(exc) or attempt >= self.max_retries:
                        if isinstance(exc, ProviderError):
                            raise
                        raise ProviderError(f"模型服务连接失败: {type(exc).__name__}: {exc}") from exc
                    delay = min(8.0, float(2**attempt))
                    logger.warning(
                        "LLM 请求失败，%.1f 秒后重试 attempt=%d/%d model=%s server=%s error=%s",
                        delay, attempt + 1, self.max_retries + 1, selected.model,
                        classify_openai_server(selected.base_url), type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
        raise ProviderError(str(last_error or "模型请求失败"))

    async def _chat_stream(
        self,
        selected: ModelConfig,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        on_delta: Callable[[str], Awaitable[None]] | None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": selected.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        headers = selected.auth_headers()
        started = time.perf_counter()
        last_error: Exception | None = None
        attempt = 0
        while attempt <= self.max_retries:
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            call_parts: dict[int, dict[str, Any]] = {}
            usage: dict[str, int] = {}
            emitted = False
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout_seconds),
                    transport=self.transport,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"{selected.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.status_code >= 400:
                            await response.aread()
                            lowered = response.text.lower()
                            if (
                                "stream_options" in payload
                                and response.status_code in {400, 422}
                                and ("stream_options" in lowered or "unknown" in lowered or "unsupported" in lowered)
                            ):
                                payload.pop("stream_options", None)
                                continue
                            self._raise_response_error(response)
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                logger.warning("忽略无法解析的 SSE 行: %s", raw[:120])
                                continue
                            if event.get("error"):
                                raise ProviderError(f"模型流返回错误: {str(event['error'])[:300]}")
                            if event.get("usage"):
                                usage = {
                                    key: int(value)
                                    for key, value in event["usage"].items()
                                    if isinstance(value, int) and not isinstance(value, bool)
                                }
                            choices = event.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            text = str(delta.get("content") or "")
                            if text:
                                emitted = True
                                content_parts.append(text)
                                if on_delta:
                                    await on_delta(text)
                            reasoning = str(delta.get("reasoning_content") or "")
                            if reasoning:
                                reasoning_parts.append(reasoning)
                            for fragment in delta.get("tool_calls") or []:
                                emitted = True
                                index = int(fragment.get("index", 0))
                                target = call_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                if fragment.get("id"):
                                    target["id"] = fragment["id"]
                                function = fragment.get("function") or {}
                                if function.get("name"):
                                    target["name"] = function["name"]
                                target["arguments"] += str(function.get("arguments") or "")
                calls, raw_calls = [], []
                for item in call_parts.values():
                    try:
                        arguments = json.loads(item["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    calls.append({"id": item["id"], "name": item["name"], "arguments": arguments})
                    raw_calls.append({
                        "id": item["id"],
                        "type": "function",
                        "function": {"name": item["name"], "arguments": item["arguments"] or "{}"},
                    })
                content = "".join(content_parts) or "".join(reasoning_parts)
                raw_message = {"role": "assistant", "content": content or None, "tool_calls": raw_calls}
                return ChatResult(content, calls, raw_message, usage, int((time.perf_counter() - started) * 1000), attempt)
            except (ContextLengthError, ContentSafetyError, asyncio.CancelledError):
                raise
            except (httpx.TimeoutException, httpx.TransportError, ProviderError) as exc:
                last_error = exc
                if emitted:
                    raise ProviderError("流式响应已输出部分内容后中断，为避免重复内容未自动重试") from exc
                if not self._retryable(exc) or attempt >= self.max_retries:
                    if isinstance(exc, ProviderError):
                        raise
                    raise ProviderError(f"模型流连接失败: {type(exc).__name__}: {exc}") from exc
                await asyncio.sleep(min(8.0, float(2**attempt)))
                attempt += 1
        raise ProviderError(str(last_error or "模型流请求失败"))

    @staticmethod
    def _raise_response_error(response: httpx.Response) -> None:
        text = response.text[:2000]
        lowered = text.lower()
        if any(hint in lowered for hint in _SAFETY_HINTS):
            raise ContentSafetyError("模型供应商拒绝了不安全内容")
        if any(hint in lowered for hint in _CONTEXT_HINTS):
            raise ContextLengthError("模型上下文超过限制")
        raise ProviderError(f"模型服务返回 HTTP {response.status_code}: {text[:300]}")

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "http 429", "http 500", "http 502", "http 503", "http 504",
                "rate limit", "temporarily unavailable",
            )
        )
