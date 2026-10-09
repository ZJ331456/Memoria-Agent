from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .assembler import (
    SYSTEM_CONTEXT_FRAME_END,
    SYSTEM_CONTEXT_FRAME_MARKER,
    compact_context_frame,
    minimum_context_frame_chars,
)


_TOOL_TRUNCATION = "\n[工具结果已按上下文预算截断]"
_USER_TRUNCATION = "\n[较长用户输入已按模型上下文预算截断，原文仍保存在会话中]\n"


@dataclass(slots=True)
class ContextBudgetResult:
    messages: list[dict[str, Any]]
    original_chars: int
    final_chars: int
    dropped_messages: int
    truncated_tool_results: int
    truncated_messages: int = 0
    preserved_context_frame: bool = False


class ContextBudget:
    """Apply a hard character budget while preserving current turn and tool protocol."""

    def __init__(self, max_chars: int = 60000, tool_result_chars: int = 12000):
        if type(max_chars) is not int or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")
        if type(tool_result_chars) is not int or tool_result_chars <= 0:
            raise ValueError("tool_result_chars must be a positive integer")
        self.max_chars = max_chars
        self.tool_result_chars = tool_result_chars

    def apply(self, messages: list[dict[str, Any]], ratio: float = 1.0) -> ContextBudgetResult:
        limit = max(1, int(self.max_chars * max(.1, min(1.0, ratio))))
        original = self._chars(messages)
        truncated, tool_count = self._truncate_tools(messages)
        prepared = self._keep_complete_tool_exchanges(truncated)

        system_index = next((i for i, item in enumerate(prepared) if item.get("role") == "system"), None)
        # The assembler places its frame immediately after the system message
        # and before the real user turn. A user can paste identical markers;
        # marker text alone must not make that later message a trusted frame.
        frame_index = None
        if system_index is not None:
            candidate = system_index + 1
            if (
                candidate < len(prepared)
                and self._is_frame(prepared[candidate])
                and any(item.get("role") == "user" for item in prepared[candidate + 1:])
            ):
                frame_index = candidate
        user_index = next(
            (i for i in range(len(prepared) - 1, -1, -1)
             if prepared[i].get("role") == "user" and i != frame_index),
            None,
        )
        protected: set[int] = set()
        if system_index is not None:
            protected.add(system_index)
        if frame_index is not None:
            protected.add(frame_index)
        if user_index is not None:
            protected.add(user_index)

        replacements: dict[int, dict[str, Any]] = {}
        system_size = self._message_chars(prepared[system_index]) if system_index is not None else 0
        if system_size > limit:
            raise ValueError("system prompt exceeds context character budget")

        frame_minimum = 0
        if frame_index is not None:
            frame = prepared[frame_index]
            frame_minimum = minimum_context_frame_chars(str(frame.get("content", "")))
            frame_minimum += self._message_chars({**frame, "content": ""})
        user_overhead = 0
        user_content = ""
        if user_index is not None:
            user = prepared[user_index]
            user_content = str(user.get("content", ""))
            user_overhead = self._message_chars({**user, "content": ""})
            if user_content:
                user_overhead += 1
        if system_size + frame_minimum + user_overhead > limit:
            raise ValueError("system prompt and essential current context exceed character budget")

        truncated_messages = 0
        user_size = 0
        if user_index is not None:
            user = prepared[user_index]
            content_allowance = limit - system_size - frame_minimum - (user_overhead - (1 if user_content else 0))
            if frame_index is not None and len(user_content) > content_allowance:
                content_allowance = min(content_allowance, max(1, (limit - system_size) // 2))
            new_content = self._clip_user(user_content, content_allowance)
            if new_content != user_content:
                truncated_messages += 1
            replacements[user_index] = {**user, "content": new_content}
            user_size = self._message_chars(replacements[user_index])

        frame_size = 0
        if frame_index is not None:
            frame = prepared[frame_index]
            available_for_frame = limit - system_size - user_size
            frame_overhead = self._message_chars({**frame, "content": ""})
            frame_content = str(frame.get("content", ""))
            new_frame = compact_context_frame(frame_content, available_for_frame - frame_overhead)
            if new_frame != frame_content:
                truncated_messages += 1
            replacements[frame_index] = {**frame, "content": new_frame}
            frame_size = self._message_chars(replacements[frame_index])

        used = system_size + user_size + frame_size
        selected = set(protected)
        units = self._protocol_units(prepared)
        for unit in reversed(units):
            if any(index in protected for index in unit):
                continue
            if any(prepared[index].get("role") == "system" or index == frame_index for index in unit):
                continue
            size = sum(self._message_chars(prepared[index]) for index in unit)
            if used + size <= limit:
                selected.update(unit)
                used += size
        result = [replacements.get(index, prepared[index]) for index in sorted(selected)]
        result = self._keep_complete_tool_exchanges(result)
        final = self._chars(result)
        if final > limit:
            raise ValueError("context character budget could not be satisfied")
        return ContextBudgetResult(
            result, original, final, len(messages) - len(result), tool_count,
            truncated_messages, frame_index is not None and frame_index in selected,
        )

    def emergency(self, messages: list[dict[str, Any]]) -> ContextBudgetResult:
        return self.apply(messages, ratio=.45)

    def _truncate_tools(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        result, count = [], 0
        for message in messages:
            clone = dict(message)
            content = str(clone.get("content", ""))
            if clone.get("role") == "tool" and len(content) > self.tool_result_chars:
                if self.tool_result_chars > len(_TOOL_TRUNCATION):
                    clone["content"] = content[:self.tool_result_chars - len(_TOOL_TRUNCATION)] + _TOOL_TRUNCATION
                else:
                    clone["content"] = content[:self.tool_result_chars]
                count += 1
            result.append(clone)
        return result, count

    @staticmethod
    def _is_frame(message: dict[str, Any]) -> bool:
        content = message.get("content")
        return (
            message.get("role") == "user"
            and isinstance(content, str)
            and content.startswith(SYSTEM_CONTEXT_FRAME_MARKER)
            and content.endswith(SYSTEM_CONTEXT_FRAME_END)
        )

    @staticmethod
    def _clip_user(content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        if limit <= 0:
            return ""
        if limit <= len(_USER_TRUNCATION) + 2:
            return content[-limit:]
        remaining = limit - len(_USER_TRUNCATION)
        head = max(1, remaining // 3)
        tail = remaining - head
        return content[:head] + _USER_TRUNCATION + content[-tail:]

    @staticmethod
    def _protocol_units(messages: list[dict[str, Any]]) -> list[list[int]]:
        units: list[list[int]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            calls = message.get("tool_calls") or []
            if message.get("role") == "assistant" and calls:
                ids = [str(call.get("id")) for call in calls if isinstance(call, dict) and call.get("id")]
                end = index + 1
                responses: list[str] = []
                while end < len(messages) and messages[end].get("role") == "tool":
                    responses.append(str(messages[end].get("tool_call_id", "")))
                    end += 1
                if ids and len(ids) == len(calls) and len(ids) == len(set(ids)) and set(responses) == set(ids) and len(responses) == len(ids):
                    units.append(list(range(index, end)))
                index = end
                continue
            if message.get("role") != "tool":
                units.append([index])
            index += 1
        return units

    @classmethod
    def _keep_complete_tool_exchanges(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [messages[index] for unit in cls._protocol_units(messages) for index in unit]

    @staticmethod
    def _message_chars(message: dict[str, Any]) -> int:
        return len(str(message.get("content", ""))) + len(str(message.get("tool_calls", ""))) + 32

    def _chars(self, messages: list[dict[str, Any]]) -> int:
        return sum(self._message_chars(message) for message in messages)
