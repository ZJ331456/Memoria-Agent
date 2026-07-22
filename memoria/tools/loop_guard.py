from __future__ import annotations

import json
from collections import Counter
from typing import Any


class ToolLoopGuard:
    """Stops the third identical tool-call batch while keeping the message chain closed."""

    def __init__(self, max_identical: int = 2):
        self.max_identical = max(1, int(max_identical))
        self._counts: Counter[str] = Counter()

    def check(self, calls: list[dict[str, Any]]) -> tuple[bool, str]:
        signature = self.signature(calls)
        if not signature: return True, ""
        self._counts[signature] += 1
        return self._counts[signature] <= self.max_identical, signature

    @staticmethod
    def signature(calls: list[dict[str, Any]]) -> str:
        return "|".join(f"{call.get('name','')}:{json.dumps(call.get('arguments') or {},ensure_ascii=False,sort_keys=True,separators=(',',':'))}" for call in calls)
