from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(value: str) -> str:
    return _ENV.sub(lambda match: os.getenv(match.group(1), ""), value)


@dataclass(slots=True)
class McpServerConfig:
    name: str
    command: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    allowed_tools: tuple[str, ...] = ()
    risk: str = "write"
    timeout_seconds: float = 30.0

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd,
            "enabled": self.enabled,
            "allowed_tools": list(self.allowed_tools),
            "risk": self.risk,
            "timeout_seconds": self.timeout_seconds,
            "env_keys": sorted(self.env),
        }


def load_mcp_servers(path: Path) -> list[McpServerConfig]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        servers = raw.get("servers", [])
    elif isinstance(raw, list):
        servers = raw
    else:
        raise ValueError("mcp_servers.json 必须是对象或数组")
    if not isinstance(servers, list):
        raise ValueError("mcp_servers.json.servers 必须是数组")
    result: list[McpServerConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(servers):
        if not isinstance(item, dict):
            raise ValueError(f"mcp server[{index}] 必须是对象")
        name = str(item.get("name", "")).strip()
        if not _NAME.fullmatch(name):
            raise ValueError(f"mcp server[{index}].name 无效: {name}")
        if name in seen:
            raise ValueError(f"mcp server 名称重复: {name}")
        seen.add(name)
        command = item.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError(f"mcp server {name}: command 必须是非空字符串数组")
        cwd = item.get("cwd")
        if cwd is not None:
            cwd = str(cwd)
            if not cwd.strip():
                cwd = None
        env_raw = item.get("env") or {}
        if not isinstance(env_raw, dict):
            raise ValueError(f"mcp server {name}: env 必须是对象")
        env = {str(key): _resolve_env(str(value)) for key, value in env_raw.items()}
        allowed = item.get("allowed_tools") or []
        if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
            raise ValueError(f"mcp server {name}: allowed_tools 必须是字符串数组")
        risk = str(item.get("risk", "write"))
        if risk not in {"read-only", "write", "external-side-effect"}:
            raise ValueError(f"mcp server {name}: risk 无效")
        result.append(
            McpServerConfig(
                name=name,
                command=tuple(str(part) for part in command),
                cwd=cwd,
                env=env,
                enabled=bool(item.get("enabled", True)),
                allowed_tools=tuple(str(value) for value in allowed),
                risk=risk,
                timeout_seconds=float(item.get("timeout_seconds", 30)),
            )
        )
    return result
