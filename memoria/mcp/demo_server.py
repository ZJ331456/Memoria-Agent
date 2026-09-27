"""最小 stdio MCP server，供本地联调与单测使用。"""

from __future__ import annotations

import json
import sys
from typing import Any


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(request_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "result": result})


def main() -> None:
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        message = json.loads(text)
        if not isinstance(message, dict):
            continue
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "memoria-demo-mcp", "version": "0.1.0"},
                },
            )
            continue
        if method == "notifications/initialized":
            continue
        if method == "tools/list":
            _result(
                request_id,
                {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "原样返回 text，用于验证 MCP 调用链路。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        },
                        {
                            "name": "add",
                            "description": "把 a 和 b 相加。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "number"},
                                    "b": {"type": "number"},
                                },
                                "required": ["a", "b"],
                                "additionalProperties": False,
                            },
                        },
                    ]
                },
            )
            continue
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                text_value = str(arguments.get("text", ""))
                _result(request_id, {"content": [{"type": "text", "text": text_value}], "isError": False})
            elif name == "add":
                total = float(arguments.get("a", 0)) + float(arguments.get("b", 0))
                _result(request_id, {"content": [{"type": "text", "text": str(total)}], "isError": False})
            else:
                _result(
                    request_id,
                    {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True},
                )
            continue
        if request_id is not None:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
