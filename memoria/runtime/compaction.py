from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from ..store import Store

logger = logging.getLogger(__name__)

COMPACTION_PROMPT = """把下列较早对话压缩成结构化摘要，供后续轮次继续使用。

只记录输入中已经出现的事实，不补充猜测，不把计划写成已完成。
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


@dataclass(slots=True)
class CompactionResult:
    summary: str
    covered_message_ids: list[str]
    reused: bool = False


class SessionCompactor:
    """Summarize messages that fall outside the active memory window."""

    def __init__(self, store: Store, summarizer: Summarizer | None = None, *, min_messages: int = 8):
        self.store = store
        self.summarizer = summarizer
        self.min_messages = max(4, int(min_messages))

    async def ensure(self, session_id: str, window: int) -> CompactionResult | None:
        messages = self.store.messages(session_id, limit=500)
        if len(messages) <= max(window, self.min_messages):
            return None
        keep = max(window, 4)
        older = messages[:-keep]
        if len(older) < 4:
            return None
        existing = self.store.session_compaction(session_id)
        covered_ids = [item["id"] for item in older]
        if existing and existing.get("covered_message_ids") == covered_ids and existing.get("summary"):
            return CompactionResult(existing["summary"], covered_ids, reused=True)
        if not self.summarizer:
            fallback = self._fallback_summary(older)
            self.store.upsert_session_compaction(session_id, fallback, covered_ids, older[-1]["id"])
            return CompactionResult(fallback, covered_ids)
        transcript = "\n".join(f"{item['role']}: {item['content']}" for item in older)
        try:
            summary = await self.summarizer.complete(
                [
                    {"role": "system", "content": COMPACTION_PROMPT},
                    {"role": "user", "content": transcript[:24000]},
                ],
                max_tokens=1200,
            )
            summary = (summary or "").strip() or self._fallback_summary(older)
        except Exception:
            logger.exception("session compaction failed; using fallback summary")
            summary = self._fallback_summary(older)
        self.store.upsert_session_compaction(session_id, summary, covered_ids, older[-1]["id"])
        return CompactionResult(summary, covered_ids)

    @staticmethod
    def _fallback_summary(messages: list[dict[str, Any]]) -> str:
        user_bits = [m["content"].strip() for m in messages if m.get("role") == "user" and m.get("content")]
        assistant_bits = [m["content"].strip() for m in messages if m.get("role") == "assistant" and m.get("content")]
        goals = "；".join(user_bits[:3])[:500] or "（未明确）"
        done = "；".join(assistant_bits[-2:])[:500] or "（暂无）"
        return (
            "## Goal\n"
            f"{goals}\n\n"
            "## Constraints & Preferences\n（压缩回退未提取）\n\n"
            "## Progress\n"
            "### Done\n"
            f"{done}\n"
            "### In Progress\n（未知）\n"
            "### Blocked\n（未知）\n\n"
            "## Key Decisions\n（压缩回退未提取）\n\n"
            "## Next Steps\n继续响应用户最新消息。\n\n"
            "## Critical Context\n较早消息已压缩；完整原文仍保存在会话历史中。"
        )
