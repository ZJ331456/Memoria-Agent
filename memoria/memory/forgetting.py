"""Periodic soft archival for personal episodic memory."""

from __future__ import annotations

import asyncio
import logging

from .episodic import EpisodicMemory


logger = logging.getLogger(__name__)


class ForgettingWorker:
    def __init__(self, episodes: EpisodicMemory, interval_seconds: int):
        self.episodes = episodes
        self.interval_seconds = max(60, int(interval_seconds))

    async def run_loop(self, stop: asyncio.Event | None = None) -> None:
        """Run maintenance now, then periodically until cancelled or stopped."""
        while stop is None or not stop.is_set():
            try:
                self.episodes.maintenance(dry_run=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("episodic forgetting maintenance failed")
            if stop is None:
                await asyncio.sleep(self.interval_seconds)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
                except asyncio.TimeoutError:
                    pass
