from __future__ import annotations

import re
from typing import Any

from .registry import Tool, ToolRegistry

_META_NAMES = frozenset({"tool_search", "tool_call"})


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _tokenize(query: str) -> set[str]:
    text = query.strip().lower()
    tokens: set[str] = {text, *text.split()}
    for part in re.split(r"([\u4e00-\u9fff]+)", text):
        part = part.strip()
        if part:
            tokens.add(part)
    cjk = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    tokens.update(cjk)
    tokens.update(left + right for left, right in zip(cjk, cjk[1:]))
    tokens.discard("")
    return tokens


class ToolPresentation:
    """按需暴露工具 schema：always_on 直连，其余经 tool_search + tool_call。"""

    def __init__(self, registry: ToolRegistry, *, enabled: bool = True):
        self.registry = registry
        self.enabled = enabled
        if enabled:
            self._ensure_search_tool()

    def _ensure_search_tool(self) -> None:
        if self.registry.get("tool_search"):
            return

        async def search(arguments: dict[str, Any]) -> dict[str, Any]:
            return self.search(
                str(arguments["query"]),
                top_k=int(arguments.get("top_k", 5)),
                allowed_risk=arguments.get("allowed_risk"),
            )

        self.registry.register(
            Tool(
                "tool_search",
                "搜索可调用工具目录；命中后用 tool_call 传入 name 与 arguments 执行完整 schema。",
                _schema(
                    {
                        "query": {"type": "string", "minLength": 1, "maxLength": 200},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                        "allowed_risk": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["read-only", "write", "external-side-effect"],
                            },
                            "maxItems": 3,
                        },
                    },
                    ["query"],
                ),
                search,
                owner="tool_search",
                search_hint="搜索工具 schema 目录 tool_call",
                always_on=True,
            )
        )

    def _groups(self) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for tool in sorted(self.registry.all(), key=lambda item: item.name):
            if tool.name in _META_NAMES:
                continue
            group = rows.setdefault(
                tool.owner,
                {"owner": tool.owner, "description": f"{tool.owner} 工具组", "tools": []},
            )
            group["tools"].append(
                {
                    "schema": tool.schema(),
                    "risk": tool.risk,
                    "search_hint": tool.search_hint,
                    "always_on": tool.always_on,
                }
            )
        return [rows[key] for key in sorted(rows)]

    def schemas(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return [tool.schema() for tool in self.registry.all() if tool.name != "tool_call"]
        direct = [
            tool.schema()
            for tool in self.registry.all()
            if tool.always_on and tool.name != "tool_call"
        ]
        direct.append(
            {
                "type": "function",
                "function": {
                    "name": "tool_call",
                    "description": "调用已通过 tool_search 获知完整 schema 的工具。",
                    "parameters": _schema(
                        {
                            "name": {"type": "string", "minLength": 1, "maxLength": 120},
                            "arguments": {"type": "object"},
                        },
                        ["name", "arguments"],
                    ),
                },
            }
        )
        return direct

    def catalog_prompt(self) -> str:
        if not self.enabled:
            return ""
        lines = [
            "## 可搜索工具目录",
            "用 tool_search 获取完整 schema，再用 tool_call 传入 name 与 arguments。always_on 工具可直接调用。",
        ]
        for group in self._groups():
            lines.append(f"{group['owner']}：{group['description']}")
            for entry in group["tools"]:
                function = entry["schema"]["function"]
                description = " ".join(str(function["description"]).split())
                short = description[:80] + ("…" if len(description) > 80 else "")
                flag = " [always_on]" if entry["always_on"] else ""
                lines.append(f"   {function['name']}{flag}：{short}")
        return "\n".join(lines)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        allowed_risk: list[str] | None = None,
    ) -> dict[str, Any]:
        tokens = _tokenize(query)
        ranked: list[tuple[int, int, str, dict[str, Any]]] = []
        for group in self._groups():
            owner = str(group["owner"])
            score = 0
            exact = False
            allowed = False
            matched_tools: list[dict[str, Any]] = []
            for entry in group["tools"]:
                risk = entry["risk"]
                if allowed_risk is not None and risk not in allowed_risk:
                    continue
                allowed = True
                function = entry["schema"]["function"]
                name = str(function["name"]).lower()
                description = str(function["description"]).lower()
                hint = str(entry.get("search_hint") or "").lower()
                exact |= owner.lower() in tokens or name in tokens
                tool_score = 0
                for token in tokens:
                    if token == owner.lower() or token == name:
                        tool_score += 10
                    elif token in owner.lower() or token in name:
                        tool_score += 5
                    if token in description:
                        tool_score += 2
                    if token and token in hint:
                        tool_score += 4
                if tool_score:
                    matched_tools.append(entry["schema"])
                score = max(score, tool_score)
            if allowed and score:
                ranked.append(
                    (
                        -int(exact),
                        -score,
                        owner,
                        {"owner": owner, "tools": matched_tools or [item["schema"] for item in group["tools"]]},
                    )
                )
        matched = [row for _, _, _, row in sorted(ranked)[: max(1, min(top_k, 10))]]
        return {
            "matched_groups": matched,
            "tip": "使用 tool_call，并传入 name 与 arguments。" if matched else "没有匹配工具，请调整关键词。",
        }

    def decode(self, name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]] | str:
        if not self.enabled:
            return name, arguments
        if name != "tool_call":
            tool = self.registry.get(name)
            if not tool:
                return f"未知工具: {name}"
            if not tool.always_on:
                return f"工具不属于当前直接调用目录: {name}；请用 tool_search 查询，再用 tool_call 调用。"
            return name, arguments
        raw_name = arguments.get("name")
        raw_args = arguments.get("arguments", {})
        if not isinstance(raw_name, str) or not raw_name.strip():
            return "tool_call 需要非空 name"
        if not isinstance(raw_args, dict):
            return "tool_call 的 arguments 必须是对象"
        tool = self.registry.get(raw_name)
        if not tool or raw_name in _META_NAMES:
            available = sorted(t.name for t in self.registry.all() if t.name not in _META_NAMES)
            return f"工具不属于获授目录: {raw_name}；可用: {', '.join(available[:20])}"
        return raw_name, raw_args

    def public_status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "direct_tools": sorted(t.name for t in self.registry.all() if t.always_on),
            "searchable_tools": sorted(t.name for t in self.registry.all() if not t.always_on and t.name not in _META_NAMES),
            "groups": [
                {"owner": group["owner"], "tools": [entry["schema"]["function"]["name"] for entry in group["tools"]]}
                for group in self._groups()
            ],
        }
