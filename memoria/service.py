from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .config import Settings
from .llm import LLMClient
from .store import Store
from .memory import EmbeddingClient, MemoryEngine, MemoryJobWorker, MarkdownMemoryLayer
from .models_config import public_models, save_model_overrides, test_model_slot
from .runtime import AgentRuntime
from .skills import SkillCatalog
from .tools import build_registry


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
        tools = build_registry(
            store,
            memory,
            self.skills,
            http_allowed_hosts=settings.http_allowed_hosts,
        )
        self.runtime = AgentRuntime(
            settings, store, llm, memory, tools, skills=self.skills, markdown=self.markdown,
        )
        self.memory_worker = MemoryJobWorker(
            store, llm, memory, lease_seconds=settings.memory_job_lease_seconds,
            max_retries=settings.memory_job_max_retries, backoff_seconds=settings.memory_job_backoff_seconds,
        )

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
        self.embedder.config = self.settings.embedding
        return result

    def models_public(self) -> dict[str, Any]:
        return public_models(self.settings)

    async def test_model(self, slot: str) -> dict[str, Any]:
        return await test_model_slot(self.settings, slot)
