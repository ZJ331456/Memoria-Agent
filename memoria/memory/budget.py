"""Shared per-turn allowance for memory and history tool reads."""

from __future__ import annotations

import threading


_MEMORY_READ_TOOLS = frozenset({"recall_memory", "recall_episodes", "search_history", "load_skill"})
_TRUNCATED = "\n[本轮记忆检索预算已截断]"
_EXHAUSTED = "[本轮记忆检索预算已用尽；此结果未作为有效记忆注入]"


class TurnMemoryBudget:
    """Account for initial context and concurrent memory-tool responses."""

    def __init__(self, max_calls: int, max_chars: int):
        if type(max_calls) is not int or max_calls < 0:
            raise ValueError("max_calls must be a nonnegative integer")
        if type(max_chars) is not int or max_chars < 0:
            raise ValueError("max_chars must be a nonnegative integer")
        self.max_calls = max_calls
        self.max_chars = max_chars
        self.calls = 0
        self.used_chars = 0
        self.blocked_calls = 0
        self._lock = threading.Lock()

    def allow_call(self, name: str) -> bool:
        if name not in _MEMORY_READ_TOOLS:
            return True
        with self._lock:
            if self.calls >= self.max_calls or self.used_chars >= self.max_chars:
                self.blocked_calls += 1
                return False
            self.calls += 1
            return True

    def account_initial(self, chars: int) -> None:
        if type(chars) is not int or chars < 0:
            raise ValueError("initial context size must be a nonnegative integer")
        with self._lock:
            if self.used_chars + chars > self.max_chars:
                raise ValueError("initial memory context exceeds per-turn retrieval budget")
            self.used_chars += chars

    def consume(self, content: str) -> str:
        content = str(content)
        with self._lock:
            available = self.max_chars - self.used_chars
            if available <= 0:
                return _EXHAUSTED
            if len(content) <= available:
                self.used_chars += len(content)
                return content
            if available <= len(_TRUNCATED):
                self.used_chars = self.max_chars
                return _EXHAUSTED
            clipped = content[:available - len(_TRUNCATED)] + _TRUNCATED
            self.used_chars += len(clipped)
            return clipped

    def public_dict(self) -> dict[str, int]:
        with self._lock:
            return {
                "used_chars": self.used_chars,
                "calls": self.calls,
                "blocked_calls": self.blocked_calls,
                "max_chars": self.max_chars,
                "max_calls": self.max_calls,
                "remaining_chars": max(0, self.max_chars - self.used_chars),
                "remaining_calls": max(0, self.max_calls - self.calls),
            }
