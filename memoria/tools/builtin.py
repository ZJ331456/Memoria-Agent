from __future__ import annotations

import ast
import operator
from datetime import datetime
from zoneinfo import ZoneInfo

from ..memory import MemoryEngine
from ..store import Store
from .registry import Tool, ToolRegistry


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def build_registry(store: Store, memory: MemoryEngine | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    async def recall(a):
        if memory:
            return await memory.retrieve(str(a["query"]), int(a.get("limit", 5)))
        return store.memories(str(a["query"]), int(a.get("limit", 5)))
    async def memorize(a):
        if memory:
            saved = await memory.add_if_new(a["content"], a.get("kind", "fact"), int(a.get("importance", 3)), "agent_tool")
            return saved or {"saved": False, "reason": "已存在相似记忆"}
        return store.add_memory(a["content"], a.get("kind", "fact"), int(a.get("importance", 3)), "agent_tool")
    async def forget(a): return {"deleted": store.delete_memory(a["memory_id"])}
    async def history(a): return store.search_messages(a["query"], int(a.get("limit", 6)))
    async def clock(a): return datetime.now(ZoneInfo(a.get("timezone", "Asia/Shanghai"))).isoformat()
    async def calculate(a): return _safe_calculate(a["expression"])
    registry.register(Tool("recall_memory", "使用关键词和语义向量检索长期记忆。", _schema({"query":{"type":"string"},"limit":{"type":"integer"}}, ["query"]), recall))
    registry.register(Tool("memorize", "明确保存一条值得长期保留的用户事实、偏好或目标。", _schema({"content":{"type":"string"},"kind":{"type":"string"},"importance":{"type":"integer"}}, ["content"]), memorize, "write"))
    registry.register(Tool("forget_memory", "按记忆 ID 删除错误或用户要求遗忘的记忆。", _schema({"memory_id":{"type":"string"}}, ["memory_id"]), forget, "write"))
    registry.register(Tool("search_history", "搜索过去会话消息。", _schema({"query":{"type":"string"},"limit":{"type":"integer"}}, ["query"]), history))
    registry.register(Tool("current_time", "获取指定 IANA 时区的当前时间。", _schema({"timezone":{"type":"string"}}, []), clock))
    registry.register(Tool("calculate", "安全计算基础算术表达式。", _schema({"expression":{"type":"string"}}, ["expression"]), calculate))
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
