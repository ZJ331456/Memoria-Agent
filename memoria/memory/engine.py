from __future__ import annotations

import re

from ..store import Store


class MemoryEngine:
    """Hybrid deterministic retrieval and duplicate suppression facade."""
    def __init__(self, store: Store): self.store = store

    def retrieve(self, query: str, limit: int = 8) -> list[dict]:
        all_items = self.store.memories(limit=500)
        terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", query.lower()))
        def score(item: dict) -> float:
            text = item["content"].lower()
            overlap = sum(1 for term in terms if term in text)
            return overlap * 10 + int(item["importance"]) + (2 if item["kind"] in {"preference","profile"} else 0)
        ranked = sorted(all_items, key=score, reverse=True)
        return [item for item in ranked if score(item)>0][:limit]

    def add_if_new(self, content: str, kind: str, importance: int, source: str) -> dict | None:
        normalized = self._normalize(content)
        for item in self.store.memories(limit=1000):
            if self._similar(normalized, self._normalize(item["content"])) >= .86: return None
        return self.store.add_memory(content, kind, importance, source)

    @staticmethod
    def _normalize(text: str) -> set[str]:
        return set(re.findall(r"[\w\u4e00-\u9fff]", text.lower()))

    @staticmethod
    def _similar(a: set[str], b: set[str]) -> float:
        return len(a & b) / max(1, len(a | b))

