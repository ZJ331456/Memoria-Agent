from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import ModelConfig

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """The configured embedding provider could not produce usable vectors."""


class EmbeddingClient:
    """Small OpenAI-compatible embedding client with bounded retries and batches."""

    def __init__(self, config: ModelConfig, timeout_seconds: float = 30, max_retries: int = 2, batch_size: int = 16):
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.batch_size = max(1, min(batch_size, 64))

    @property
    def enabled(self) -> bool:
        return bool(self.config.model and self.config.api_key and self.config.base_url)

    async def embed(self, text: str) -> list[float] | None:
        vectors = await self.embed_batch([text])
        return vectors[0] if vectors else None

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts or not self.enabled:
            return []
        result: list[list[float]] = []
        for offset in range(0, len(texts), self.batch_size):
            chunk = [text[:8000] for text in texts[offset:offset + self.batch_size]]
            result.extend(await self._request(chunk))
        return result

    async def _request(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.config.model, "input": texts, "encoding_format": "float"}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(f"{self.config.base_url}/embeddings", headers=headers, json=payload)
                    if response.status_code >= 400:
                        raise EmbeddingError(f"embedding 服务返回 HTTP {response.status_code}: {response.text[:300]}")
                    return self._vectors(response.json(), len(texts))
                except (httpx.TimeoutException, httpx.TransportError, EmbeddingError, KeyError, TypeError, ValueError) as exc:
                    last_error = exc
                    retryable = isinstance(exc, (httpx.TimeoutException, httpx.TransportError)) or any(
                        token in str(exc).lower() for token in ("http 429", "http 500", "http 502", "http 503", "http 504")
                    )
                    if not retryable or attempt >= self.max_retries:
                        break
                    await asyncio.sleep(min(4.0, float(2**attempt)))
        raise EmbeddingError(f"embedding 请求失败: {type(last_error).__name__}: {last_error}") from last_error

    @staticmethod
    def _vectors(payload: dict[str, Any], expected: int) -> list[list[float]]:
        data = sorted(payload["data"], key=lambda item: int(item.get("index", 0)))
        vectors = [[float(value) for value in item["embedding"]] for item in data]
        if len(vectors) != expected or any(not vector for vector in vectors):
            raise EmbeddingError(f"embedding 响应数量异常，期望 {expected}，实际 {len(vectors)}")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise EmbeddingError("embedding 响应向量维度不一致")
        return vectors
