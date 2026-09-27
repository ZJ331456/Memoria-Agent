from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

Listener = Callable[[str, dict[str, Any]], Awaitable[None] | None]


@dataclass(slots=True)
class EventBus:
    """In-process fan-out bus. Listeners never mutate the turn path."""

    _listeners: list[Listener] = field(default_factory=list)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def clear(self) -> None:
        self._listeners.clear()

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        data = dict(payload or {})
        data.setdefault("event", event)
        for listener in list(self._listeners):
            try:
                result = listener(event, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("event bus listener failed for %s", event)
