from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..store import Store
from .embedding import EmbeddingClient

logger = logging.getLogger(__name__)
MemoryDecider = Callable[[str, str, list[dict[str, Any]]], Awaitable[dict[str, str]]]
_STOP_TOKENS = {"我的", "什么", "是什么", "用户", "现在", "偏好", "目标", "信息", "the", "what", "your", "user"}


@dataclass(slots=True)
class MemoryWriteResult:
    action: str
    memory: dict[str, Any] | None
    previous_id: str | None = None
    reason: str = ""

    def public_dict(self) -> dict[str, Any]:
        memory = dict(self.memory) if self.memory else None
        if memory:
            memory.pop("embedding", None)
        return {"action": self.action, "memory": memory, "previous_id": self.previous_id, "reason": self.reason}


class MemoryEngine:
    """Keyword + vector retrieval with RRF fusion and graceful lexical fallback."""

    def __init__(self, store: Store, embedder: EmbeddingClient | None = None, decider: MemoryDecider | None = None, vector_scan_limit: int = 2000):
        self.store = store
        self.embedder = embedder
        self.decider = decider
        self.vector_scan_limit = max(100, vector_scan_limit)

    async def retrieve(self, query: str, limit: int = 8, kinds: set[str] | None = None) -> list[dict]:
        lexical_seed = self.store.keyword_memory_candidates(query, limit=200) if query.strip() else []
        indexed = self.store.vector_index_status["enabled"]
        items = self.store.memories(limit=200 if indexed else self.vector_scan_limit)
        if kinds:
            items = [item for item in items if item.get("kind") in kinds]
            lexical_seed = [item for item in lexical_seed if item.get("kind") in kinds]
        if not query.strip():
            return []
        by_id = {item["id"]: item for item in [*items, *lexical_seed]}
        items = list(by_id.values())

        query_vector: list[float] | None = None
        if self.embedder and self.embedder.enabled:
            await self._backfill(items, limit=64)
            try:
                query_vector = await asyncio.wait_for(self.embedder.embed(query), timeout=self.embedder.timeout_seconds + 1)
            except Exception as exc:
                logger.warning("语义记忆召回降级为关键词召回: %s", type(exc).__name__)

        vector_scores: dict[str, float] = {}
        if query_vector:
            if indexed:
                for item, similarity in self.store.vector_memory_candidates(query_vector, max(limit * 8, 64)):
                    if not kinds or item.get("kind") in kinds:
                        by_id[item["id"]] = item
                        vector_scores[item["id"]] = similarity
                items = list(by_id.values())
            else:
                for item in items:
                    vector = item.get("embedding")
                    if vector and len(vector) == len(query_vector):
                        vector_scores[item["id"]] = self._cosine(query_vector, vector)
        query_tokens = self._tokens(query)
        lexical_scores = {item["id"]: self._lexical_score(query, query_tokens, item) for item in items}
        lexical_ids = {item["id"] for item in lexical_seed}
        lexical = sorted(
            (item for item in items if lexical_scores[item["id"]] > 0 or item["id"] in lexical_ids),
            key=lambda item: (item["id"] in lexical_ids, lexical_scores[item["id"]]), reverse=True,
        )
        top_similarity = max(vector_scores.values(), default=-1.0)
        semantic_floor = max(0.45, top_similarity - 0.18)
        semantic = sorted((item for item in items if vector_scores.get(item["id"], -1) >= semantic_floor), key=lambda item: vector_scores[item["id"]], reverse=True)

        fused: dict[str, float] = {}
        lanes: dict[str, list[str]] = {"keyword": [item["id"] for item in lexical], "vector": [item["id"] for item in semantic]}
        for lane, weight in ((lexical, 0.8), (semantic, 1.0)):
            for rank, item in enumerate(lane, start=1):
                fused[item["id"]] = fused.get(item["id"], 0.0) + weight / (60 + rank)
        by_id = {item["id"]: item for item in items}
        ranked_ids = sorted(
            fused,
            key=lambda memory_id: (
                fused[memory_id] + min(math.log1p(int(by_id[memory_id].get("reinforcement", 1))), 2.5) * 0.0005,
                int(by_id[memory_id]["importance"]),
            ),
            reverse=True,
        )
        ranked_ids = self._apply_kind_quotas(ranked_ids, by_id, max(1, limit))
        result = []
        for memory_id in ranked_ids:
            item = dict(by_id[memory_id])
            item.pop("embedding", None)
            item["retrieval"] = {
                "score": round(fused[memory_id], 6),
                "keyword_rank": self._rank(lanes["keyword"], memory_id),
                "vector_rank": self._rank(lanes["vector"], memory_id),
                "vector_similarity": round(vector_scores[memory_id], 4) if memory_id in vector_scores else None,
            }
            result.append(item)
        return result

    async def add_if_new(self, content: str, kind: str, importance: int, source: str, source_ref: str | None = None) -> dict | None:
        result = await self.remember(content, kind, importance, source, source_ref)
        return result.memory if result.action in {"created", "superseded"} else None

    async def remember(self, content: str, kind: str, importance: int, source: str, source_ref: str | None = None) -> MemoryWriteResult:
        content = content.strip()
        if not content:
            return MemoryWriteResult("skipped", None, reason="empty content")
        indexed = self.store.vector_index_status["enabled"]
        seeded = self.store.keyword_memory_candidates(content, limit=200)
        items = list({item["id"]: item for item in [*seeded, *self.store.memories(limit=200 if indexed else 1000)]}.values())
        canonical = self._canonical(content)
        for item in items:
            if item["kind"] == kind and canonical == self._canonical(item["content"]):
                if source_ref and self.store.has_memory_operation(source_ref, item["id"]):
                    return MemoryWriteResult("skipped", item, item["id"], "source already applied")
                reinforced = self.store.reinforce_memory(item["id"], source_ref)
                return MemoryWriteResult("reinforced", reinforced, item["id"], "exact match")

        vector: list[float] | None = None
        if self.embedder and self.embedder.enabled:
            try:
                await self._backfill(items, limit=128)
                vector = await asyncio.wait_for(self.embedder.embed(content), timeout=self.embedder.timeout_seconds + 1)
            except Exception as exc:
                logger.warning("记忆向量去重不可用，使用文本去重: %s", type(exc).__name__)
                vector = None

        if vector and indexed:
            indexed_candidates = [item for item, _ in self.store.vector_memory_candidates(vector, 64)]
            items = list({item["id"]: item for item in [*items, *indexed_candidates]}.values())

        normalized = self._normalize(content)
        related: list[dict[str, Any]] = []
        for item in items:
            if item["kind"] != kind:
                continue
            lexical_similarity = self._similar(normalized, self._normalize(item["content"]))
            semantic_similarity = 0.0
            existing = item.get("embedding")
            if vector and existing and len(existing) == len(vector):
                semantic_similarity = self._cosine(vector, existing)
            relation_similarity = max(lexical_similarity, semantic_similarity)
            if relation_similarity >= 0.55:
                candidate = dict(item)
                candidate.pop("embedding", None)
                candidate["relation_similarity"] = round(relation_similarity, 4)
                related.append(candidate)
        related.sort(key=lambda item: float(item["relation_similarity"]), reverse=True)
        related = related[:3]

        if related and self.decider:
            decision = await self.decider(content, kind, related)
            target_id = decision.get("target_id", "")
            target = next((item for item in related if item["id"] == target_id), None)
            action = decision.get("action", "create")
            if target and action == "reinforce" and float(target["relation_similarity"]) >= 0.78:
                if source_ref and self.store.has_memory_operation(source_ref, target_id):
                    return MemoryWriteResult("skipped", target, target_id, "source already applied")
                reinforced = self.store.reinforce_memory(target_id, source_ref)
                return MemoryWriteResult("reinforced", reinforced, target_id, decision.get("reason", ""))
            mutable_kinds = {"preference", "profile", "goal", "procedure"}
            if target and action == "supersede" and kind in mutable_kinds and float(target["relation_similarity"]) >= 0.55:
                reason = decision.get("reason", "")
                saved = self.store.add_memory(content, kind, importance, source, vector, target_id, reason, source_ref)
                return MemoryWriteResult("superseded", saved, target_id, reason)

        saved = self.store.add_memory(content, kind, importance, source, vector, source_ref=source_ref)
        return MemoryWriteResult("created", saved, reason="independent memory")

    async def reindex(self, limit: int = 1000) -> dict[str, int | bool]:
        items = self.store.memories(limit=max(1, min(limit, 5000)))
        missing = [item for item in items if not item.get("embedding")]
        if not self.embedder or not self.embedder.enabled:
            return {"enabled": False, "indexed": 0, "remaining": len(missing)}
        indexed = await self._backfill(missing, limit=len(missing))
        return {"enabled": True, "indexed": indexed, "remaining": max(0, len(missing) - indexed)}

    async def _backfill(self, items: list[dict], limit: int) -> int:
        if not self.embedder or not self.embedder.enabled:
            return 0
        missing = [item for item in items if not item.get("embedding")][:limit]
        if not missing:
            return 0
        try:
            vectors = await self.embedder.embed_batch([item["content"] for item in missing])
        except Exception as exc:
            logger.warning("记忆向量回填失败: %s", type(exc).__name__)
            return 0
        indexed = 0
        for item, vector in zip(missing, vectors, strict=False):
            if vector:
                self.store.set_memory_embedding(item["id"], vector)
                item["embedding"] = vector
                indexed += 1
        return indexed

    @staticmethod
    def _tokens(text: str) -> set[str]:
        lowered = text.lower()
        tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
        for sequence in re.findall(r"[\u4e00-\u9fff]+", lowered):
            tokens.update(sequence[index:index + 2] for index in range(max(1, len(sequence) - 1)))
            if len(sequence) <= 4:
                tokens.add(sequence)
        return {token for token in tokens if token and token not in _STOP_TOKENS}

    @classmethod
    def _lexical_score(cls, query: str, query_tokens: set[str], item: dict) -> float:
        text = item["content"].lower()
        item_tokens = cls._tokens(text)
        overlap = len(query_tokens & item_tokens)
        exact = 8 if query.lower().strip() in text else 0
        type_bonus = 0.3 if item["kind"] in {"preference", "profile"} else 0
        return overlap * 2 + exact + int(item["importance"]) * 0.05 + type_bonus if overlap or exact else 0

    @staticmethod
    def _normalize(text: str) -> set[str]:
        return set(re.findall(r"[\w\u4e00-\u9fff]", text.lower()))

    @staticmethod
    def _canonical(text: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())

    @staticmethod
    def _similar(a: set[str], b: set[str]) -> float:
        return len(a & b) / max(1, len(a | b))

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        denominator = math.sqrt(sum(value * value for value in a)) * math.sqrt(sum(value * value for value in b))
        return sum(left * right for left, right in zip(a, b, strict=False)) / denominator if denominator else 0.0

    @staticmethod
    def _rank(ids: list[str], memory_id: str) -> int | None:
        try:
            return ids.index(memory_id) + 1
        except ValueError:
            return None

    @staticmethod
    def _apply_kind_quotas(ids: list[str], by_id: dict[str, dict[str, Any]], limit: int) -> list[str]:
        quotas = {"profile": 3, "preference": 3, "fact": 4, "goal": 3, "procedure": 2}
        counts: dict[str, int] = {}
        selected: list[str] = []
        for memory_id in ids:
            kind = str(by_id[memory_id].get("kind", "fact"))
            if counts.get(kind, 0) >= quotas.get(kind, limit):
                continue
            selected.append(memory_id)
            counts[kind] = counts.get(kind, 0) + 1
            if len(selected) >= limit:
                break
        return selected
