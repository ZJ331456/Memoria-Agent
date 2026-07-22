from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable


class Phase(StrEnum):
    BEFORE_TURN = "before_turn"
    BEFORE_REASONING = "before_reasoning"
    AFTER_STEP = "after_step"
    AFTER_REASONING = "after_reasoning"
    AFTER_TURN = "after_turn"


@dataclass(slots=True)
class TurnContext:
    session_id: str
    user_text: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    tool_chain: list[dict[str, Any]] = field(default_factory=list)
    response: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[TurnContext], Awaitable[None]]


class Pipeline:
    """Small deterministic phase runner; modules run by priority then registration order."""
    def __init__(self) -> None:
        self._handlers: dict[Phase, list[tuple[int, Handler]]] = {phase: [] for phase in Phase}

    def register(self, phase: Phase, handler: Handler, priority: int = 100) -> None:
        self._handlers[phase].append((priority, handler))
        self._handlers[phase].sort(key=lambda item: item[0])

    async def run(self, phase: Phase, context: TurnContext) -> None:
        context.metadata.setdefault("phases", []).append(phase.value)
        for _, handler in self._handlers[phase]:
            await handler(context)

    def inspect(self) -> dict[str, list[str]]:
        return {phase.value: [getattr(h, "__name__", type(h).__name__) for _, h in handlers] for phase, handlers in self._handlers.items()}

