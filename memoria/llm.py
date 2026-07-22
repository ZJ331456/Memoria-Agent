from __future__ import annotations

import json
import re
import asyncio
import logging
from typing import Any

import httpx
from dataclasses import dataclass, field

from .config import ModelConfig, Settings

logger = logging.getLogger(__name__)
_CONTEXT_HINTS = ("context_length_exceeded", "maximum context length", "context window", "range of input length", "too many tokens")
_SAFETY_HINTS = ("content_filter", "content_policy_violation", "data_inspection_failed")


class ProviderError(RuntimeError): pass
class ContextLengthError(ProviderError): pass
class ContentSafetyError(ProviderError): pass


@dataclass(slots=True)
class ChatResult:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def complete(self, messages: list[dict[str, str]], model: ModelConfig | None = None, max_tokens: int | None = None) -> str:
        result = await self.chat(messages, model=model, max_tokens=max_tokens)
        return result.content

    async def chat(self, messages: list[dict[str, Any]], model: ModelConfig | None = None, max_tokens: int | None = None, tools: list[dict[str, Any]] | None = None) -> ChatResult:
        selected = model or self.settings.main
        if not selected.api_key or not selected.base_url or not selected.model:
            raise RuntimeError("主模型未完整配置，请检查 config.toml")
        payload: dict[str, Any] = {"model": selected.model, "messages": messages, "max_tokens": max_tokens or self.settings.max_tokens, "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {selected.api_key}", "Content-Type": "application/json"}
        data = await self._post(selected, headers, payload)
        message = data["choices"][0]["message"]
        # DeepSeek reasoning 型模型在部分兼容端点只填 reasoning_content。
        # 正常 content 优先；否则回退 reasoning_content，避免成功请求得到空回复。
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try: arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError: arguments = {}
            calls.append({"id": call.get("id", ""), "name": fn.get("name", ""), "arguments": arguments})
        return ChatResult(str(message.get("content") or message.get("reasoning_content") or ""), calls, message)

    async def _post(self, selected: ModelConfig, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(self.settings.max_retries + 1):
                try:
                    response = await client.post(f"{selected.base_url}/chat/completions", headers=headers, json=payload)
                    if response.status_code >= 400:
                        self._raise_response_error(response)
                    return response.json()
                except (ContextLengthError, ContentSafetyError):
                    raise
                except (httpx.TimeoutException, httpx.TransportError, ProviderError) as exc:
                    last_error = exc
                    retryable = self._retryable(exc)
                    if not retryable or attempt >= self.settings.max_retries:
                        if isinstance(exc, ProviderError): raise
                        raise ProviderError(f"模型服务连接失败: {type(exc).__name__}: {exc}") from exc
                    delay = min(8.0, float(2**attempt))
                    logger.warning("LLM 请求失败，%.1f 秒后重试 attempt=%d/%d model=%s error=%s", delay, attempt+1, self.settings.max_retries+1, selected.model, type(exc).__name__)
                    await asyncio.sleep(delay)
        raise ProviderError(str(last_error or "模型请求失败"))

    @staticmethod
    def _raise_response_error(response: httpx.Response) -> None:
        text = response.text[:2000]
        lowered = text.lower()
        if any(hint in lowered for hint in _SAFETY_HINTS): raise ContentSafetyError("模型供应商拒绝了不安全内容")
        if any(hint in lowered for hint in _CONTEXT_HINTS): raise ContextLengthError("模型上下文超过限制")
        raise ProviderError(f"模型服务返回 HTTP {response.status_code}: {text[:300]}")

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)): return True
        text = str(exc).lower()
        return any(token in text for token in ("http 429", "http 500", "http 502", "http 503", "http 504", "rate limit", "temporarily unavailable"))

    async def extract_memories(self, user_text: str, assistant_text: str) -> list[dict[str, Any]]:
        prompt = """从下面一轮对话中提取值得长期记忆、未来确实有帮助的用户事实或偏好。不要记临时问题、敏感凭据或助手说的话。仅返回 JSON 数组，每项格式 {\"content\":\"...\",\"kind\":\"preference|profile|fact|goal\",\"importance\":1到5}；没有则返回 []。"""
        try:
            raw = await self.complete([{"role": "system", "content": prompt}, {"role": "user", "content": f"用户：{user_text}\n助手：{assistant_text}"}], self.settings.fast if self.settings.fast.api_key else self.settings.main, 600)
            match = re.search(r"\[[\s\S]*\]", raw)
            parsed = json.loads(match.group(0) if match else raw)
            return [x for x in parsed if isinstance(x, dict) and str(x.get("content", "")).strip()][:3]
        except Exception:
            return []
