from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Planner = Callable[[str, list[dict[str, Any]]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class RetrievalPlan:
    needed: bool
    query: str
    kinds: set[str] = field(default_factory=set)
    limit: int = 8
    reason: str = "heuristic"

    def public_dict(self) -> dict[str, Any]:
        return {"needed": self.needed, "query": self.query, "kinds": sorted(self.kinds), "limit": self.limit, "reason": self.reason}


class MemoryQueryPlanner:
    """Cheap gating first, optional fast-model rewrite for ambiguous requests."""

    _GREETINGS = {"hi", "hello", "你好", "您好", "嗨", "谢谢", "thanks", "在吗"}
    _MEMORY_HINTS = re.compile(r"记得|以前|上次|我的|我喜欢|我偏好|我计划|remember|previous|my\b", re.I)
    _GENERAL_HINTS = re.compile(r"^(请)?(解释|介绍|说明|总结)|什么是|怎么实现|如何实现|how\s+to|what\s+is", re.I)

    def __init__(self, planner: Planner | None = None):
        self.planner = planner

    async def plan(self, query: str, history: list[dict[str, Any]]) -> RetrievalPlan:
        cleaned = query.strip()
        if not cleaned or cleaned.lower().strip("!?。！？，, ") in self._GREETINGS:
            return RetrievalPlan(False, cleaned, reason="greeting_or_empty")
        if len(cleaned) <= 3 and not self._MEMORY_HINTS.search(cleaned):
            return RetrievalPlan(False, cleaned, reason="short_query")
        if self._GENERAL_HINTS.search(cleaned) and not self._MEMORY_HINTS.search(cleaned):
            return RetrievalPlan(False, cleaned, reason="general_knowledge")
        fallback = RetrievalPlan(True, cleaned, reason="memory_hint" if self._MEMORY_HINTS.search(cleaned) else "default")
        if not self.planner:
            return fallback
        try:
            data = await self.planner(cleaned, history[-6:])
            kinds = {str(kind) for kind in data.get("kinds", []) if kind in {"fact", "preference", "profile", "goal", "procedure"}}
            needed = data.get("needed") if isinstance(data.get("needed"), bool) else fallback.needed
            return RetrievalPlan(
                needed, str(data.get("query") or cleaned)[:300], kinds,
                max(1, min(int(data.get("limit", 8)), 12)), str(data.get("reason", "fast_model"))[:100],
            )
        except Exception:
            return fallback
