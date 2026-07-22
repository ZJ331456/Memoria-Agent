from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ContextBudgetResult:
    messages: list[dict[str, Any]]
    original_chars: int
    final_chars: int
    dropped_messages: int
    truncated_tool_results: int


class ContextBudget:
    """Character-based prompt budget that preserves protocol-valid recent context."""

    def __init__(self, max_chars: int = 60000, tool_result_chars: int = 12000):
        self.max_chars = max(4000, int(max_chars))
        self.tool_result_chars = max(1000, int(tool_result_chars))

    def apply(self, messages: list[dict[str, Any]], ratio: float = 1.0) -> ContextBudgetResult:
        limit = max(4000, int(self.max_chars * max(.1, min(1.0, ratio))))
        prepared, truncated = self._truncate_tools(messages)
        original = self._chars(messages)
        if self._chars(prepared) <= limit:
            return ContextBudgetResult(prepared, original, self._chars(prepared), 0, truncated)
        system = [m for m in prepared if m.get("role") == "system"][:1]
        history = [m for m in prepared if m.get("role") != "system"]
        kept: list[dict[str, Any]] = []
        used = self._chars(system)
        # Walk complete messages backwards. Tool pairs are kept recent-first; old dangling
        # tool messages are removed by _drop_orphan_tool_messages afterwards.
        for message in reversed(history):
            size = self._message_chars(message)
            if used + size > limit and kept:
                continue
            kept.append(message)
            used += size
            if used >= limit:
                break
        result = [*system, *reversed(kept)]
        result = self._keep_complete_tool_exchanges(result)
        return ContextBudgetResult(result, original, self._chars(result), len(prepared)-len(result), truncated)

    def emergency(self, messages: list[dict[str, Any]]) -> ContextBudgetResult:
        return self.apply(messages, ratio=.45)

    def _truncate_tools(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        result, count = [], 0
        for message in messages:
            clone = dict(message)
            if clone.get("role") == "tool" and len(str(clone.get("content", ""))) > self.tool_result_chars:
                clone["content"] = str(clone["content"])[:self.tool_result_chars] + "\n[工具结果已按上下文预算截断]"
                count += 1
            result.append(clone)
        return result, count

    @staticmethod
    def _keep_complete_tool_exchanges(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response_ids = {str(m.get("tool_call_id")) for m in messages if m.get("role") == "tool" and m.get("tool_call_id")}
        valid_call_ids: set[str] = set()
        invalid_assistants: set[int] = set()
        for index, message in enumerate(messages):
            calls = message.get("tool_calls") or []
            if message.get("role") != "assistant" or not calls: continue
            ids = {str(call.get("id")) for call in calls if call.get("id")}
            if ids and ids.issubset(response_ids): valid_call_ids |= ids
            else: invalid_assistants.add(index)
        return [message for index,message in enumerate(messages) if index not in invalid_assistants and (message.get("role") != "tool" or str(message.get("tool_call_id")) in valid_call_ids)]

    @staticmethod
    def _message_chars(message: dict[str, Any]) -> int:
        return len(str(message.get("content", ""))) + len(str(message.get("tool_calls", ""))) + 32

    def _chars(self, messages: list[dict[str, Any]]) -> int:
        return sum(self._message_chars(message) for message in messages)
