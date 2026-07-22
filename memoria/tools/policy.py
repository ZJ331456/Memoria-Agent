from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class ToolAuthorization:
    allowed_write_tools: set[str] = field(default_factory=set)
    reason: str = "no explicit write intent"

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_write_tools


class ToolPolicy:
    """Map explicit user intent to the narrowest write-tool capability."""

    _MEMORIZE = re.compile(r"记住|保存.{0,8}(偏好|信息|记忆)|添加记忆|remember|memorize", re.I)
    _FORGET = re.compile(r"忘掉|忘记|删除.{0,8}(记忆|信息|偏好)|forget|delete.{0,8}memory", re.I)

    def authorize(self, user_text: str) -> ToolAuthorization:
        allowed = set()
        if self._MEMORIZE.search(user_text):
            allowed.add("memorize")
        if self._FORGET.search(user_text):
            allowed.add("forget_memory")
        reason = "explicit user intent: " + ",".join(sorted(allowed)) if allowed else "no explicit write intent"
        return ToolAuthorization(allowed, reason)
