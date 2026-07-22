from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from ..llm import LLMClient
from ..store import Store
from .engine import MemoryEngine

logger = logging.getLogger(__name__)


class MemoryJobWorker:
    """Durable, retryable post-turn extraction and consolidation worker."""

    def __init__(self, store: Store, llm: LLMClient, memory: MemoryEngine, poll_seconds: float = 0.5, lease_seconds: int = 180, max_retries: int = 3, backoff_seconds: int = 5):
        self.store, self.llm, self.memory = store, llm, memory
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.owner = uuid.uuid4().hex

    async def process_once(self) -> bool:
        job = self.store.claim_memory_job(self.owner, self.lease_seconds, self.max_retries)
        if not job:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(job["id"]))
        try:
            extracted = await self.llm.extract_memories(job["user_text"], job["assistant_text"])
            for item in extracted:
                if not self.store.renew_memory_job(job["id"], self.owner, self.lease_seconds):
                    raise RuntimeError("memory job lease lost before write")
                await self.memory.add_if_new(
                    str(item["content"]), str(item.get("kind", "fact")), int(item.get("importance", 3)),
                    "conversation", job["source_ref"],
                )
            self.store.finish_memory_job(job["id"], owner=self.owner, max_retries=self.max_retries, backoff_seconds=self.backoff_seconds)
        except asyncio.CancelledError:
            self.store.finish_memory_job(job["id"], "worker cancelled", self.owner, self.max_retries, self.backoff_seconds)
            raise
        except Exception as exc:
            logger.exception("记忆后台任务失败 job=%s", job["id"])
            self.store.finish_memory_job(job["id"], f"{type(exc).__name__}: {exc}", self.owner, self.max_retries, self.backoff_seconds)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def run(self) -> None:
        while True:
            if not await self.process_once():
                await asyncio.sleep(self.poll_seconds)

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(max(5, self.lease_seconds // 3))
            if not self.store.renew_memory_job(job_id, self.owner, self.lease_seconds):
                return
