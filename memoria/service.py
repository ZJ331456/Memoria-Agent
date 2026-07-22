from __future__ import annotations

from .config import Settings
from .llm import LLMClient
from .store import Store
from .memory import MemoryEngine
from .runtime import AgentRuntime
from .tools import build_registry


class AgentService:
    def __init__(self, settings: Settings, store: Store, llm: LLMClient):
        self.settings, self.store, self.llm = settings, store, llm
        self.runtime = AgentRuntime(settings, store, llm, MemoryEngine(store), build_registry(store))

    async def chat(self, session_id: str, content: str) -> tuple[dict, list[dict]]:
        message, memories, _ = await self.chat_with_trace(session_id, content)
        return message, memories

    async def chat_with_trace(self, session_id: str, content: str) -> tuple[dict, list[dict], dict]:
        if not self.store.session(session_id): raise KeyError(session_id)
        return await self.runtime.run(session_id, content)
