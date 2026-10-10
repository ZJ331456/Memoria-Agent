from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .config import Settings
from .drift import DriftWorker
from .llm import LLMClient
from .store import Store
from .memory import EmbeddingClient, MemoryEngine, MemoryJobWorker, MarkdownMemoryLayer
from .memory.episodic import EpisodicMemory
from .memory.forgetting import ForgettingWorker
from .mcp import McpHost
from .models_config import public_models, save_model_overrides, test_model_slot
from .memory.embedding import EmbeddingError
from .runtime import AgentRuntime
from .skills import SkillCatalog
from .tools import ToolPresentation, build_registry

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self, settings: Settings, store: Store, llm: LLMClient):
        self.settings, self.store, self.llm = settings, store, llm
        self.markdown = MarkdownMemoryLayer(settings.markdown_directory, settings.markdown_enabled)
        self.embedder = EmbeddingClient(settings.embedding, min(settings.request_timeout_seconds, 30), settings.max_retries)
        memory = MemoryEngine(
            store,
            self.embedder,
            llm.decide_memory_relation,
            settings.vector_scan_limit,
            markdown=self.markdown,
        )
        memory.refresh_markdown()
        self.skills = (
            SkillCatalog(settings.skills_directory, max_inject=settings.skills_max_inject)
            if settings.skills_enabled
            else None
        )
        layers = getattr(settings, "memory_layers", None)
        self.episodes = EpisodicMemory(store, layers) if layers and layers.enabled and layers.episodic_enabled else None
        self.forgetting_worker = (
            ForgettingWorker(self.episodes, layers.maintenance_interval_seconds)
            if self.episodes else None
        )
        tools = build_registry(
            store,
            memory,
            self.skills,
            http_allowed_hosts=settings.http_allowed_hosts,
            episodes=self.episodes,
        )
        self.tool_presentation = ToolPresentation(tools, enabled=settings.tool_search_enabled)
        self.mcp = McpHost(tools, settings.mcp_config_file, enabled=settings.mcp_enabled)
        self.runtime = AgentRuntime(
            settings,
            store,
            llm,
            memory,
            tools,
            skills=self.skills,
            markdown=self.markdown,
            tool_presentation=self.tool_presentation,
            episodes=self.episodes,
        )
        self.memory_worker = MemoryJobWorker(
            store, llm, memory, lease_seconds=settings.memory_job_lease_seconds,
            max_retries=settings.memory_job_max_retries, backoff_seconds=settings.memory_job_backoff_seconds,
        )
        self._busy_check: Callable[[], bool] = lambda: False
        self.drift = DriftWorker(
            store=store,
            runtime=self.runtime,
            skills=self.skills,
            markdown=self.markdown,
            settings=settings,
            is_busy=lambda: self._busy_check(),
        )

    def set_busy_check(self, check: Callable[[], bool]) -> None:
        self._busy_check = check

    async def start_integrations(self) -> None:
        if self.embedder.is_local and self.embedder.enabled:
            logger.info("正在预热本地 Embedding，首次启动需要加载模型")
        try:
            await self.embedder.warmup()
        except EmbeddingError as exc:
            # Keep setup available so the user can repair a missing model/device.
            logger.warning("本地 Embedding 启动预热失败：%s", exc)
        else:
            if self.embedder.is_local and self.embedder.enabled:
                logger.info("本地 Embedding 预热完成")
        if self.settings.main.is_ready(role="llm"):
            from .llm import ProviderError
            from .llm.backends.resolve import backend_public_label

            label = backend_public_label(self.settings.main)
            if self.settings.main.is_local_process():
                logger.info("正在预热进程内本地 LLM（%s）", label)
                try:
                    await self.llm.chat([{"role": "user", "content": "ping"}], max_tokens=8)
                except (ProviderError, RuntimeError, OSError, ImportError) as exc:
                    logger.warning("本地 LLM 启动预热失败：%s", exc)
                else:
                    logger.info("本地 LLM 预热完成")
            elif self.settings.main.is_local_http():
                logger.info(
                    "主模型使用 OpenAI 兼容 HTTP（%s → %s）；请确保已启动 scripts/serve_local_llm.py 或 vLLM/SGLang/Ollama",
                    label,
                    self.settings.main.base_url,
                )
        await self.mcp.start()

    async def stop_integrations(self) -> None:
        await self.mcp.stop()

    async def chat(self, session_id: str, content: str) -> tuple[dict, list[dict]]:
        message, memories, _ = await self.chat_with_trace(session_id, content)
        return message, memories

    async def chat_with_trace(
        self,
        session_id: str,
        content: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        request_id: str | None = None,
    ) -> tuple[dict, list[dict], dict]:
        if not self.store.session(session_id):
            raise KeyError(session_id)
        return await self.runtime.run(session_id, content, on_event, request_id=request_id)

    def update_models(self, updates: dict[str, dict[str, str]]) -> dict[str, Any]:
        result = save_model_overrides(self.settings, updates)
        # In-flight embedding requests keep their original client/model identity.
        self.embedder = EmbeddingClient(self.settings.embedding, min(self.settings.request_timeout_seconds, 30), self.settings.max_retries)
        self.runtime.memory.embedder = self.embedder
        if self.embedder.enabled:
            self.store.configure_embedding(self.embedder.namespace)
        return result

    def models_public(self) -> dict[str, Any]:
        return public_models(self.settings)

    async def test_model(self, slot: str) -> dict[str, Any]:
        return await test_model_slot(self.settings, slot)
