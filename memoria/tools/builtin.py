from __future__ import annotations

import ast
import operator
from datetime import datetime
from zoneinfo import ZoneInfo

from ..memory import MemoryEngine
from ..skills import SkillCatalog
from ..store import Store
from .http_get import DEFAULT_ALLOWED_HOSTS, fetch_url
from .registry import Tool, ToolRegistry


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def build_registry(
    store: Store,
    memory: MemoryEngine | None = None,
    skills: SkillCatalog | None = None,
    *,
    http_allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS,
) -> ToolRegistry:
    registry = ToolRegistry()
    async def recall(a):
        if memory:
            return await memory.retrieve(str(a["query"]), int(a.get("limit", 5)))
        return store.memories(str(a["query"]), int(a.get("limit", 5)))
    async def memorize(a):
        if memory:
            result = await memory.remember(a["content"], a.get("kind", "fact"), int(a.get("importance", 3)), "agent_tool")
            return result.public_dict()
        return store.add_memory(a["content"], a.get("kind", "fact"), int(a.get("importance", 3)), "agent_tool")
    async def forget(a): return {"deleted": store.delete_memory(a["memory_id"])}
    async def history(a): return store.search_messages(a["query"], int(a.get("limit", 6)))
    async def clock(a): return datetime.now(ZoneInfo(a.get("timezone", "Asia/Shanghai"))).isoformat()
    async def calculate(a): return _safe_calculate(a["expression"])
    async def load_skill(a):
        if not skills:
            return {"error": "skills 未启用"}
        record = skills.get(str(a["name"]))
        if not record:
            return {"error": f"未找到技能：{a['name']}", "available": [item.name for item in skills.list()]}
        if not record.available:
            return {"error": f"技能不可用：{record.name}", "missing": record.missing}
        return {
            "name": record.name,
            "description": record.description,
            "triggers": list(record.triggers),
            "base_directory": str(record.root_dir) if record.root_dir else "",
            "instructions": record.body,
        }
    async def http_get(a):
        return await fetch_url(
            str(a["url"]),
            allowed_hosts=http_allowed_hosts,
            max_chars=int(a.get("max_chars", 8000)),
        )
    registry.register(Tool(
        "recall_memory", "使用关键词和语义向量检索长期记忆。",
        _schema({"query":{"type":"string","minLength":1,"maxLength":300},"limit":{"type":"integer","minimum":1,"maximum":20}}, ["query"]),
        recall, search_hint="记忆 召回 检索 recall", always_on=True,
    ))
    registry.register(Tool(
        "memorize", "明确保存一条值得长期保留的用户事实、偏好或目标。",
        _schema({"content":{"type":"string","minLength":1,"maxLength":4000},"kind":{"type":"string","enum":["fact","preference","profile","goal","procedure"]},"importance":{"type":"integer","minimum":1,"maximum":5}}, ["content"]),
        memorize, "write", search_hint="记住 保存记忆 memorize", always_on=False,
    ))
    registry.register(Tool(
        "forget_memory", "按记忆 ID 删除错误或用户要求遗忘的记忆。",
        _schema({"memory_id":{"type":"string","minLength":1,"maxLength":64}}, ["memory_id"]),
        forget, "write", search_hint="遗忘 删除记忆 forget", always_on=False,
    ))
    registry.register(Tool(
        "search_history", "搜索过去会话消息。",
        _schema({"query":{"type":"string","minLength":1,"maxLength":300},"limit":{"type":"integer","minimum":1,"maximum":20}}, ["query"]),
        history, search_hint="历史 会话消息 history", always_on=False,
    ))
    registry.register(Tool(
        "current_time", "获取指定 IANA 时区的当前时间。",
        _schema({"timezone":{"type":"string","maxLength":64}}, []),
        clock, search_hint="时间 时钟 timezone", always_on=True,
    ))
    registry.register(Tool(
        "calculate", "安全计算基础算术表达式。",
        _schema({"expression":{"type":"string","minLength":1,"maxLength":120}}, ["expression"]),
        calculate, search_hint="计算 算术 math", always_on=True,
    ))
    registry.register(Tool(
        "load_skill", "按名称加载 skills 目录中的完整技能说明书。",
        _schema({"name":{"type":"string","minLength":1,"maxLength":80}}, ["name"]),
        load_skill, search_hint="技能 skill 说明书", always_on=True,
    ))
    registry.register(Tool(
        "http_get", "从白名单主机拉取只读 HTTP 文本，供天气/摘要等技能使用。",
        _schema({"url":{"type":"string","minLength":8,"maxLength":2000},"max_chars":{"type":"integer","minimum":200,"maximum":20000}}, ["url"]),
        http_get, timeout_seconds=20, search_hint="http 天气 fetch url", always_on=False,
    ))
    return registry


def _safe_calculate(expression: str) -> int | float:
    ops = {ast.Add:operator.add, ast.Sub:operator.sub, ast.Mult:operator.mul, ast.Div:operator.truediv, ast.FloorDiv:operator.floordiv, ast.Mod:operator.mod, ast.Pow:operator.pow, ast.USub:operator.neg, ast.UAdd:operator.pos}
    def visit(node):
        if isinstance(node, ast.Expression): return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int,float)): return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops: return ops[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            left,right=visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Pow) and abs(right)>10: raise ValueError("指数过大")
            return ops[type(node.op)](left,right)
        raise ValueError("只允许基础算术")
    if len(expression)>120: raise ValueError("表达式过长")
    return visit(ast.parse(expression, mode="eval"))
