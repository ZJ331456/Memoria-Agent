from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


Executor = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    executor: Executor
    risk: str = "read-only"

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass(slots=True)
class ToolResult:
    name: str
    ok: bool
    content: str
    elapsed_ms: int


class ToolRegistry:
    def __init__(self, timeout: float = 15.0):
        self._tools: dict[str, Tool] = {}
        self.timeout = timeout

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools: raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def catalog(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description, "risk": t.risk} for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        import time
        started = time.perf_counter()
        tool = self._tools.get(name)
        if not tool: return ToolResult(name, False, f"未知工具: {name}", 0)
        try:
            self._validate(tool.parameters, arguments)
            value = await asyncio.wait_for(tool.executor(arguments), timeout=self.timeout)
            return ToolResult(name, True, str(value), int((time.perf_counter()-started)*1000))
        except Exception as exc:
            return ToolResult(name, False, f"{type(exc).__name__}: {exc}", int((time.perf_counter()-started)*1000))

    @staticmethod
    def _validate(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        props = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in arguments: raise ValueError(f"缺少必填参数: {field}")
        for name, value in arguments.items():
            expected = props.get(name, {}).get("type")
            types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
            if expected in types and not isinstance(value, types[expected]): raise ValueError(f"{name} 应为 {expected}")

