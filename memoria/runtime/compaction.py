from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from ..store import Store

logger = logging.getLogger(__name__)

COMPACTION_PROMPT = """把下列较早对话压缩成结构化摘要，供后续轮次继续使用。

只记录输入中已经出现的事实，不补充猜测，不把计划写成已完成。
如果提供了先前摘要，请在此基础上补充新消息，不要丢失仍然有效的早期约束和决定。
标记为 part 的消息尚未完整输入；只总结本次看见的片段，不推断缺失部分。
用户消息是理解目标、要求、偏好和关系的核心证据；助手消息只记录判断与执行过程。
必须使用以下标题，不得增加标题：
## Goal
## Constraints & Preferences
## Progress
### Done
### In Progress
### Blocked
## Key Decisions
## Next Steps
## Critical Context

保留路径、符号、错误、数值和重要结论。省略重复探索与无用寒暄。只输出摘要正文。
"""


class Summarizer(Protocol):
    async def complete(self, messages: list[dict[str, str]], model: Any = None, max_tokens: int | None = None) -> str: ...


class _ConcurrentCompaction(Exception):
    """The stored cursor changed while a summarizer was running."""


@dataclass(slots=True)
class CompactionResult:
    summary: str
    covered_message_ids: list[str]
    reused: bool = False
    cursor_message_id: str | None = None
    partial_message_id: str | None = None
    partial_offset: int = 0
    pending_messages: int = 0


class SessionCompactor:
    """Summarize messages that fall outside the active memory window."""

    def __init__(
        self,
        store: Store,
        summarizer: Summarizer | None = None,
        *,
        min_messages: int = 8,
        max_batch_messages: int = 80,
        max_batches: int = 2,
        max_input_chars: int = 24000,
        max_summary_chars: int = 6000,
    ):
        self.store = store
        self.summarizer = summarizer
        self.min_messages = max(4, int(min_messages))
        self.max_batch_messages = max(1, min(int(max_batch_messages), 80))
        self.max_batches = max(1, min(int(max_batches), 8))
        self.max_input_chars = max(1024, min(int(max_input_chars), 24000))
        # Leave space for at least one new fragment, even with a small input cap.
        self.max_summary_chars = max(
            192, min(int(max_summary_chars), 6000, self.max_input_chars - len(COMPACTION_PROMPT) - 320)
        )

    async def ensure(self, session_id: str, window: int) -> CompactionResult | None:
        for _ in range(8):
            try:
                return await self._ensure_once(session_id, window)
            except _ConcurrentCompaction:
                continue
        raise RuntimeError("session compaction changed repeatedly; retry later")

    async def _ensure_once(self, session_id: str, window: int) -> CompactionResult | None:
        keep = max(window, 4)
        existing = self.store.session_compaction(session_id)
        version = str(existing["updated_at"]) if existing else None
        summary = str(existing.get("summary") or "") if existing else ""
        covered_ids = list(existing.get("covered_message_ids") or []) if existing else []
        cursor = existing.get("cursor_message_id") if existing else None
        partial_id = existing.get("partial_message_id") if existing else None
        partial_offset = int(existing.get("partial_offset") or 0) if existing else 0
        if not summary and (covered_ids or cursor or partial_id):
            if not self.store.clear_session_compaction(session_id, version):
                raise _ConcurrentCompaction
            version = None
            covered_ids, cursor, partial_id, partial_offset = [], None, None, 0
        changed = False

        for _ in range(self.max_batches):
            page = self.store.compaction_batch(
                session_id, keep,
                cursor_message_id=cursor,
                covered_message_ids=covered_ids,
                limit=self.max_batch_messages,
            )
            if not page["coverage_valid"]:
                # Old versions could skip the first 500 messages. A deleted source
                # or a foreign-session cursor likewise invalidates the whole summary.
                if not self.store.clear_session_compaction(session_id, version):
                    raise _ConcurrentCompaction
                version = None
                summary, covered_ids, cursor, partial_id, partial_offset = "", [], None, None, 0
                page = self.store.compaction_batch(session_id, keep, limit=self.max_batch_messages)
            candidates = page["messages"]
            if partial_id and (
                not candidates or candidates[0]["id"] != partial_id
                or partial_offset <= 0 or partial_offset >= len(candidates[0]["content"])
            ):
                # The source of a partial summary changed or disappeared.
                if not self.store.clear_session_compaction(session_id, version):
                    raise _ConcurrentCompaction
                version = None
                summary, covered_ids, cursor, partial_id, partial_offset = "", [], None, None, 0
                page = self.store.compaction_batch(session_id, keep, limit=self.max_batch_messages)
                candidates = page["messages"]
            if summary and len(summary) > self.max_summary_chars:
                # Old fallback rows may contain an unbounded chain of summaries.
                # Persist the bounded form even when no new message is eligible.
                bounded = self._bound_summary(summary, [])
                next_version = self.store.try_replace_session_compaction(
                    session_id, version, bounded, covered_ids, cursor,
                    keep_last=keep, partial_message_id=partial_id,
                    partial_offset=partial_offset,
                )
                if next_version is None:
                    raise _ConcurrentCompaction
                summary, version, changed = bounded, next_version, True
            if not summary and (
                page["eligible_count"] < 4
                or page["eligible_count"] + keep <= max(window, self.min_messages)
            ):
                return None
            if not candidates:
                break
            user_input, submitted, complete_ids, next_partial_id, next_partial_offset = self._prepare_batch(
                summary, candidates, partial_id, partial_offset
            )
            if not submitted:
                break
            if self.summarizer:
                try:
                    response = await self.summarizer.complete(
                        [{"role": "system", "content": COMPACTION_PROMPT},
                         {"role": "user", "content": user_input}],
                        max_tokens=1200,
                    )
                    next_summary = (response or "").strip()
                except Exception:
                    logger.exception("session compaction failed; using fallback summary")
                    next_summary = ""
            else:
                next_summary = ""
            if not next_summary:
                next_summary = self._fallback_summary(submitted, summary)
            summary = self._bound_summary(next_summary, submitted)
            covered_ids.extend(complete_ids)
            if complete_ids:
                cursor = complete_ids[-1]
            partial_id, partial_offset = next_partial_id, next_partial_offset
            next_version = self.store.try_replace_session_compaction(
                session_id, version, summary, covered_ids, cursor,
                keep_last=keep,
                partial_message_id=partial_id, partial_offset=partial_offset,
            )
            if next_version is None:
                raise _ConcurrentCompaction
            version = next_version
            changed = True

        if not summary:
            return None
        page = self.store.compaction_batch(
            session_id, keep, cursor_message_id=cursor,
            covered_message_ids=covered_ids, limit=1,
        )
        return CompactionResult(
            summary, covered_ids, reused=not changed,
            cursor_message_id=cursor, partial_message_id=partial_id,
            partial_offset=partial_offset,
            pending_messages=max(0, page["eligible_count"] - len(covered_ids)),
        )

    def _prepare_batch(
        self,
        previous_summary: str,
        messages: list[dict[str, Any]],
        partial_id: str | None,
        partial_offset: int,
    ) -> tuple[str, list[dict[str, Any]], list[str], str | None, int]:
        prefix = (
            f"先前摘要：\n{previous_summary}\n\n本批新增消息：\n"
            if previous_summary else "本批较早消息：\n"
        )
        remaining = self.max_input_chars - len(COMPACTION_PROMPT) - len(prefix)
        parts: list[str] = []
        submitted: list[dict[str, Any]] = []
        complete_ids: list[str] = []
        next_partial_id: str | None = None
        next_partial_offset = 0
        for item in messages:
            offset = partial_offset if item["id"] == partial_id else 0
            content = str(item["content"])
            header = f"\n[{item['role']} id={item['id']}]\n"
            text = content[offset:]
            required = len(header) + len(text) + (1 if parts else 0)
            if required <= remaining:
                parts.append(header + text)
                submitted.append({**item, "content": text})
                complete_ids.append(item["id"])
                remaining -= required
                continue
            if parts:
                break
            # A single oversized source is carried across turns. Its ID enters
            # covered_message_ids only after the final slice was actually read.
            reserve = len(f"\n[{item['role']} id={item['id']} part={offset}:{len(content)}/{len(content)}]\n")
            take = min(len(text) - 1, remaining - reserve)
            if take <= 0:
                break
            next_partial_offset = offset + take
            next_partial_id = item["id"]
            fragment = f"\n[{item['role']} id={item['id']} part={offset}:{next_partial_offset}/{len(content)}]\n{text[:take]}"
            parts.append(fragment)
            submitted.append({**item, "content": text[:take]})
            break
        return prefix + "\n".join(parts), submitted, complete_ids, next_partial_id, next_partial_offset

    def _bound_summary(self, summary: str, submitted: list[dict[str, Any]]) -> str:
        if len(summary) <= self.max_summary_chars:
            return summary
        # Preserve named fields rather than slicing away the newest decisions.
        return self._fallback_summary(submitted, summary)

    def _fallback_summary(
        self, messages: list[dict[str, Any]], previous_summary: str = ""
    ) -> str:
        def section(title: str) -> str:
            marker = f"## {title}\n"
            if marker not in previous_summary:
                return ""
            return previous_summary.split(marker, 1)[1].split("\n## ", 1)[0].strip()

        user_bits = [str(m["content"]).strip() for m in messages if m.get("role") == "user" and m.get("content")]
        assistant_bits = [str(m["content"]).strip() for m in messages if m.get("role") == "assistant" and m.get("content")]
        goal = section("Goal") or (user_bits[0] if user_bits else previous_summary[:200]) or "（未明确）"
        constraints = section("Constraints & Preferences") or "（未提取）"
        done = "；".join(assistant_bits[-2:]) or section("Progress") or "（暂无）"
        decisions = "；".join(filter(None, [section("Key Decisions"), *user_bits[-2:]])) or "（未提取）"
        critical = "规则回退摘要有损；完整原文仍在会话历史。" + (section("Critical Context") or previous_summary[-200:])
        skeleton = (
            "## Goal\n{goal}\n\n## Constraints & Preferences\n{constraints}\n\n"
            "## Progress\n### Done\n{done}\n### In Progress\n（未知）\n"
            "### Blocked\n（未知）\n\n## Key Decisions\n{decisions}\n\n"
            "## Next Steps\n继续响应用户最新消息。\n\n## Critical Context\n{critical}"
        )
        available = max(0, self.max_summary_chars - len(skeleton.format(
            goal="", constraints="", done="", decisions="", critical=""
        )))
        quotas = [available * share // 100 for share in (25, 30, 10, 25, 10)]
        quotas[0] += available - sum(quotas)
        return skeleton.format(
            goal=goal[:quotas[0]],
            constraints=constraints[:quotas[1]],
            done=done[-quotas[2]:] if quotas[2] else "",
            decisions=decisions[-quotas[3]:] if quotas[3] else "",
            critical=critical[:quotas[4]],
        )
