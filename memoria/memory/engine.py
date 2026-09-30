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
from .layer import MarkdownMemoryLayer

logger = logging.getLogger(__name__)
MemoryDecider = Callable[[str, str, list[dict[str, Any]]], Awaitable[dict[str, str]]]
_STOP_TOKENS = {"我的", "什么", "是什么", "用户", "现在", "偏好", "目标", "信息", "the", "what", "your", "user"}


@dataclass(slots=True)
class MemoryWriteResult:
    action: str
    memory: dict[str, Any] | None
    previous_id: str | None = None
    reason: str = ""
    valid_at: str | None = None
    invalid_at: str | None = None

    def public_dict(self) -> dict[str, Any]:
        memory = dict(self.memory) if self.memory else None
        if memory:
            memory.pop("embedding", None)
        return {"action": self.action, "memory": memory, "previous_id": self.previous_id, "reason": self.reason}


class MemoryEngine:
    """Keyword + vector retrieval with RRF fusion and graceful lexical fallback."""

    def __init__(
        self,
        store: Store,
        embedder: EmbeddingClient | None = None,
        decider: MemoryDecider | None = None,
        vector_scan_limit: int = 2000,
        markdown: MarkdownMemoryLayer | None = None,
    ):
        self.store = store
        self.embedder = embedder
        self.decider = decider
        self.vector_scan_limit = max(100, vector_scan_limit)
        self.markdown = markdown

    @staticmethod
    def organize(content: str, kind: str) -> dict[str, Any]:
        entities = sorted(set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Z][a-zA-Z0-9_-]{1,30}", content)))[:8]
        keywords = sorted(MemoryEngine._tokens(content))[:12]
        return {"entities": entities, "keywords": keywords, "topic": (content[:24] or kind),
                "confidence": 0.9 if entities else 0.6, "organized_by": "agent"}

    def auto_link(self, memory_id: str, entities: list[str], limit: int = 10) -> int:
        if not entities: return 0
        count = 0
        for item in self.store.memories(limit=200):
            if item["id"] == memory_id: continue
            other = set(item.get("entities") or []) or self._tokens(item["content"])
            overlap = len(set(entities) & set(other)) if other else 0
            lex = self._similar(self._normalize(" ".join(entities)), self._normalize(item["content"]))
            if overlap > 0 or lex >= 0.12:
                self.store.add_memory_link(memory_id, item["id"], "related", min(1.0, 0.4 + overlap * 0.2 + lex))
                count += 1
                if count >= limit: break
        return count

    async def evolve(self, memory_id: str, new_info: str, reason: str = "") -> dict[str, Any] | None:
        current = self.store.memory(memory_id)
        if not current or current["status"] != "active": return None
        merged = f"{current['content']}；{new_info.strip()}"
        result = await self.remember(merged, current["kind"], int(current["importance"]), "evolved", None)
        if result.memory:
            self.store.record_evolution(result.memory["id"], f"由 {memory_id} 演化: {reason or new_info[:120]}")
        return result.memory

    async def retrieve(self, query: str, limit: int = 8, kinds: set[str] | None = None, as_of: str | None = None, expand_graph: bool = True) -> list[dict]:
        lexical_seed = self.store.keyword_memory_candidates(query, limit=200) if query.strip() else []
        indexed = self.store.vector_index_status["enabled"]
        items = self.store.memories(limit=200 if indexed else self.vector_scan_limit)
        if kinds:
            items = [item for item in items if item.get("kind") in kinds]
            lexical_seed = [item for item in lexical_seed if item.get("kind") in kinds]
        if as_of:
            items = self.store.time_travel(as_of, limit=max(1, limit * 3), query=query.strip())
            if kinds: items = [i for i in items if i.get("kind") in kinds]
            return [{k: v for k, v in i.items() if k != "embedding"} for i in items[:limit]]
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

        doc_freq: dict[str, int] = {}
        tokenized = {item["id"]: self._tokens(item["content"]) for item in items}
        for toks in tokenized.values():
            for token in toks: doc_freq[token] = doc_freq.get(token, 0) + 1
        avg_len = sum(len(t) for t in tokenized.values()) / max(1, len(tokenized))
        bm25_scores = {mid: self._bm25(query_tokens, toks, len(toks), avg_len, doc_freq, max(1, len(tokenized))) for mid, toks in tokenized.items()}
        bm25_ranked = sorted((mid for mid, sc in bm25_scores.items() if sc > 0), key=lambda m: bm25_scores[m], reverse=True)
        fused: dict[str, float] = {}
        lanes: dict[str, list[str]] = {"keyword": [item["id"] for item in lexical], "vector": [item["id"] for item in semantic], "bm25": bm25_ranked}
        for lane, weight in ((lexical, 0.8), (semantic, 1.0), ([by_id[m] for m in bm25_ranked if m in by_id], 0.9)):
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
        if expand_graph and ranked_ids:
            try:
                seen = set(ranked_ids)
                for mid in list(ranked_ids)[:3]:
                    for nb in self.store.memory_neighbors(mid, depth=1, limit=3):
                        if nb["id"] not in seen and (not kinds or nb.get("kind") in kinds):
                            seen.add(nb["id"]); by_id[nb["id"]] = nb
                            fused[nb["id"]] = fused.get(nb["id"], 0.0) + 0.15 * fused.get(mid, 0.0)
                            ranked_ids.append(nb["id"])
            except Exception: pass
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
        result = await self._remember(content, kind, importance, source, source_ref)
        self._sync_markdown_layer(result, content=content, kind=kind, source_ref=source_ref)
        return result

    async def _remember(self, content: str, kind: str, importance: int, source: str, source_ref: str | None = None) -> MemoryWriteResult:
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
            if target and target.get("source") == "user_correction" and source not in {"manual", "reviewed_conversation"} and action == "supersede":
                return MemoryWriteResult("skipped", target, target_id, "user correction requires explicit review")
            if target and action == "reinforce" and float(target["relation_similarity"]) >= 0.78:
                if source_ref and self.store.has_memory_operation(source_ref, target_id):
                    return MemoryWriteResult("skipped", target, target_id, "source already applied")
                reinforced = self.store.reinforce_memory(target_id, source_ref)
                return MemoryWriteResult("reinforced", reinforced, target_id, decision.get("reason", ""))
            mutable_kinds = {"preference", "profile", "goal", "procedure"}
            if target and action == "supersede" and kind in mutable_kinds and float(target["relation_similarity"]) >= 0.55:
                reason = decision.get("reason", "")
                saved = self.store.add_memory(content, kind, importance, source, vector, target_id, reason, source_ref)
                try:
                    self.store.temporal_invalidate(target_id, saved["id"])
                    organized = self.organize(content, kind)
                    self.store.db.execute("UPDATE memories SET valid_at=COALESCE(valid_at,created_at), attributes_json=?, entities_json=?, provenance_json=? WHERE id=?",
                        (__import__("json").dumps(organized, ensure_ascii=False), __import__("json").dumps(organized["entities"], ensure_ascii=False), __import__("json").dumps({"source": source, "source_ref": source_ref, "supersedes": target_id}, ensure_ascii=False), saved["id"]))
                    self.store.db.commit()
                    self.auto_link(saved["id"], organized["entities"])
                    saved = self.store.memory(saved["id"]) or saved
                except Exception: pass
                return MemoryWriteResult("superseded", saved, target_id, reason)

        if not self.decider:
            for item in related:
                full = self.store.memory(item["id"])
                if full and full.get("status") == "active" and kind in {"preference", "profile", "goal", "procedure"} and self._contradicts(content, full["content"]):
                    saved = self.store.add_memory(content, kind, importance, source, vector, item["id"], "contradiction auto-supersede", source_ref)
                    try:
                        self.store.temporal_invalidate(item["id"], saved["id"])
                        self.store.record_evolution(saved["id"], f"矛盾替代 {item['id']}: {content[:120]}")
                    except Exception: pass
                    return MemoryWriteResult("superseded", saved, item["id"], "contradiction auto-supersede")
        saved = self.store.add_memory(content, kind, importance, source, vector, source_ref=source_ref)
        try:
            organized = self.organize(content, kind)
            self.store.db.execute("UPDATE memories SET valid_at=COALESCE(valid_at,created_at), attributes_json=?, entities_json=?, provenance_json=? WHERE id=?",
                (__import__("json").dumps(organized, ensure_ascii=False), __import__("json").dumps(organized["entities"], ensure_ascii=False), __import__("json").dumps({"source": source, "source_ref": source_ref}, ensure_ascii=False), saved["id"]))
            self.store.db.commit()
            self.auto_link(saved["id"], organized["entities"])
            saved = self.store.memory(saved["id"]) or saved
        except Exception: pass
        return MemoryWriteResult("created", saved, reason="independent memory")

    def refresh_markdown(self) -> str:
        if not self.markdown or not self.markdown.enabled:
            return ""
        return self.markdown.sync_memory(self.store.memories(limit=5000))

    async def correct(self, memory_id: str, content: str, kind: str, importance: int, reason: str) -> dict[str, Any] | None:
        current = self.store.memory(memory_id)
        if not current or current["status"] != "active":
            return None
        vector = current.get("embedding") if content.strip() == current["content"] else None
        if vector is None and self.embedder and self.embedder.enabled:
            try:
                vector = await asyncio.wait_for(self.embedder.embed(content), timeout=self.embedder.timeout_seconds + 1)
            except Exception as exc:
                logger.warning("纠正记忆向量化不可用，使用关键词检索: %s", type(exc).__name__)
        corrected = self.store.correct_memory(memory_id, content, kind, importance, reason, vector)
        if corrected:
            self.refresh_markdown()
        return corrected

    def _sync_markdown_layer(
        self,
        result: MemoryWriteResult,
        *,
        content: str,
        kind: str,
        source_ref: str | None,
    ) -> None:
        if not self.markdown or not self.markdown.enabled:
            return
        if result.action in {"created", "reinforced", "superseded"}:
            self.markdown.sync_memory(self.store.memories(limit=5000))
            if source_ref:
                self.markdown.complete_pending(source_ref=source_ref, content=content, kind=kind)

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
    def _bm25(query_tokens: set[str], item_tokens: set[str], doc_len: int, avg_len: float, doc_freq: dict[str, int], total_docs: int) -> float:
        score = 0.0
        for token in query_tokens & item_tokens:
            df = doc_freq.get(token, 1)
            idf = max(0.0, __import__("math").log((total_docs - df + 0.5) / (df + 0.5) + 1.0))
            tf = 1.0
            score += idf * (tf * 2.2) / (tf + 1.2 * (1 - 0.75 + 0.75 * (doc_len / max(1.0, avg_len))))
        return score

    @staticmethod
    def _contradicts(a: str, b: str) -> bool:
        neg = ("不", "没", "否", "不是", "不再", "讨厌", "反对", "not ", "n't ", "never ", "no longer ")
        ax, bx = a.strip().lower(), b.strip().lower()
        if MemoryEngine._similar(MemoryEngine._normalize(ax), MemoryEngine._normalize(bx)) < 0.45:
            return False
        return any(n in ax for n in neg) != any(n in bx for n in neg)

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
