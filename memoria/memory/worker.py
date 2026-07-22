from __future__ import annotations

import asyncio
import logging

from ..llm import LLMClient
from ..store import Store
from .engine import MemoryEngine

logger = logging.getLogger(__name__)


class MemoryJobWorker:
    """Durable, retryable post-turn extraction and consolidation worker."""

    def __init__(self, store: Store, llm: LLMClient, memory: MemoryEngine, poll_seconds: float = 0.5):
        self.store, self.llm, self.memory = store, llm, memory
        self.poll_seconds = poll_seconds

    async def process_once(self) -> bool:
        job = self.store.claim_memory_job()
        if not job:
            return False
        try:
            extracted = await self.llm.extract_memories(job["user_text"], job["assistant_text"])
            for item in extracted:
                await self.memory.add_if_new(
                    str(item["content"]), str(item.get("kind", "fact")), int(item.get("importance", 3)),
                    "conversation", job["source_ref"],
                )
            self.store.finish_memory_job(job["id"])
        except asyncio.CancelledError:
            self.store.finish_memory_job(job["id"], "worker cancelled")
            raise
        except Exception as exc:
            logger.exception("记忆后台任务失败 job=%s", job["id"])
            self.store.finish_memory_job(job["id"], f"{type(exc).__name__}: {exc}")
        return True

    async def run(self) -> None:
        while True:
            if not await self.process_once():
                await asyncio.sleep(self.poll_seconds)
