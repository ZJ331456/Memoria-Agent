from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools.registry import Tool, ToolRegistry
from .client import McpClient, McpToolExecutionError
from .config import McpServerConfig, load_mcp_servers

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^a-zA-Z0-9_]+")


def local_tool_name(server: str, remote: str) -> str:
    safe_server = _SAFE.sub("_", server).strip("_") or "mcp"
    safe_tool = _SAFE.sub("_", remote).strip("_") or "tool"
    return f"mcp_{safe_server}_{safe_tool}"


@dataclass(slots=True)
class McpServerStatus:
    name: str
    enabled: bool
    connected: bool
    tools: list[str] = field(default_factory=list)
    error: str = ""
    command: list[str] = field(default_factory=list)
    risk: str = "write"

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "connected": self.connected,
            "tools": list(self.tools),
            "error": self.error,
            "command": list(self.command),
            "risk": self.risk,
        }


class McpHost:
    """管理多个 stdio MCP server，并把工具注册进 ToolRegistry。"""

    def __init__(
        self,
        registry: ToolRegistry,
        config_path: Path,
        *,
        enabled: bool = True,
    ):
        self.registry = registry
        self.config_path = config_path
        self.enabled = enabled
        self._clients: dict[str, McpClient] = {}
        self._configs: dict[str, McpServerConfig] = {}
        self._status: dict[str, McpServerStatus] = {}
        self._registered: dict[str, list[str]] = {}

    def load_configs(self) -> list[McpServerConfig]:
        if not self.enabled:
            return []
        configs = load_mcp_servers(self.config_path)
        self._configs = {item.name: item for item in configs}
        return configs

    async def start(self) -> list[McpServerStatus]:
        if not self.enabled:
            return []
        self.load_configs()
        return await self.connect_all()

    async def connect_all(self) -> list[McpServerStatus]:
        statuses: list[McpServerStatus] = []
        for config in self._configs.values():
            statuses.append(await self._connect_one(config))
        return statuses

    async def reload(self) -> list[McpServerStatus]:
        await self.stop()
        return await self.start()

    async def stop(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                await client.disconnect()
            except Exception:
                logger.exception("[mcp] 断开 %s 失败", name)
            self._unregister_server(name)
        self._clients.clear()
        self._status.clear()

    async def _connect_one(self, config: McpServerConfig) -> McpServerStatus:
        status = McpServerStatus(
            name=config.name,
            enabled=config.enabled,
            connected=False,
            command=list(config.command),
            risk=config.risk,
        )
        if not config.enabled:
            status.error = "disabled"
            self._status[config.name] = status
            return status
        client = McpClient(config.name, config.command, cwd=config.cwd, env=config.env)
        try:
            infos = await client.connect()
            if config.allowed_tools:
                allow = set(config.allowed_tools)
                infos = [item for item in infos if item.name in allow]
            self._clients[config.name] = client
            registered = self._register_tools(config, client, infos)
            status.connected = True
            status.tools = registered
            status.error = ""
        except Exception as exc:
            status.error = f"{type(exc).__name__}: {exc}"
            logger.warning("[mcp] 连接 %s 失败: %s", config.name, status.error)
            try:
                await client.disconnect()
            except Exception:
                pass
        self._status[config.name] = status
        return status

    def _register_tools(
        self,
        config: McpServerConfig,
        client: McpClient,
        infos: list,
    ) -> list[str]:
        self._unregister_server(config.name)
        owner = f"mcp:{config.name}"
        names: list[str] = []
        for info in infos:
            local_name = local_tool_name(config.name, info.name)
            if self.registry.get(local_name):
                logger.warning("[mcp] 跳过重名工具 %s", local_name)
                continue
            remote_name = info.name
            timeout = config.timeout_seconds

            async def executor(
                arguments: dict[str, Any],
                *,
                _client: McpClient = client,
                _remote: str = remote_name,
                _timeout: float = timeout,
            ) -> str:
                try:
                    return await _client.call(_remote, arguments, timeout=_timeout)
                except McpToolExecutionError as exc:
                    raise RuntimeError(str(exc)) from exc

            parameters = dict(info.input_schema)
            parameters.setdefault("type", "object")
            parameters.setdefault("properties", {})
            self.registry.register(
                Tool(
                    local_name,
                    info.description or f"MCP {config.name}/{info.name}",
                    parameters,
                    executor,
                    risk=config.risk,
                    timeout_seconds=timeout,
                    owner=owner,
                    search_hint=f"mcp {config.name} {info.name}",
                    always_on=False,
                )
            )
            names.append(local_name)
        self._registered[config.name] = names
        return names

    def _unregister_server(self, name: str) -> None:
        owner = f"mcp:{name}"
        self.registry.unregister_owner(owner)
        self._registered.pop(name, None)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "config_path": str(self.config_path),
            "servers": [item.public_dict() for item in self._status.values()]
            or [cfg.public_dict() | {"connected": False, "tools": [], "error": "not started"} for cfg in self._configs.values()],
            "tool_count": sum(len(names) for names in self._registered.values()),
        }
