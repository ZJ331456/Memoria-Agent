from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .memory.layer_settings import MemoryLayerSettings

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve(value: str) -> str:
    return _ENV.sub(lambda m: os.getenv(m.group(1), ""), value)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _string_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        raise ValueError("allowed_origins 必须是字符串或字符串数组")
    result = tuple(str(item).strip().rstrip("/") for item in values if str(item).strip())
    return result or default


def _int_tuple(value: Any, default: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return default
    values = [value] if isinstance(value, (int, float, str)) else value
    if not isinstance(values, (list, tuple)):
        raise ValueError("quiet_hours 必须是整数或整数数组")
    result: list[int] = []
    for item in values:
        hour = int(item)
        if hour < 0 or hour > 23:
            raise ValueError("quiet_hours 必须在 0–23")
        result.append(hour)
    return tuple(result) if result else default


@dataclass(slots=True)
class ModelConfig:
    model: str
    api_key: str
    base_url: str


@dataclass(slots=True)
class Settings:
    root: Path
    database: Path
    main: ModelConfig
    fast: ModelConfig
    embedding: ModelConfig
    system_prompt: str
    max_tokens: int
    max_iterations: int
    memory_window: int
    context_char_budget: int
    request_timeout_seconds: float
    max_retries: int
    vector_backend: str
    vector_scan_limit: int
    memory_job_lease_seconds: int
    memory_job_max_retries: int
    memory_job_backoff_seconds: int
    api_token: str
    allowed_origins: tuple[str, ...]
    rate_limit_per_minute: int
    max_request_bytes: int
    metrics_enabled: bool
    skills_enabled: bool
    skills_directory: Path
    skills_max_inject: int
    http_allowed_hosts: tuple[str, ...]
    tool_search_enabled: bool
    mcp_enabled: bool
    mcp_config_file: Path
    drift_enabled: bool
    drift_min_idle_seconds: int
    drift_interval_seconds: int
    drift_max_steps: int
    drift_daily_budget: int
    drift_quiet_hours: tuple[int, ...]
    drift_allowed_skills: tuple[str, ...]
    drift_allow_write_tools: tuple[str, ...]
    drift_timezone: str
    markdown_enabled: bool
    markdown_directory: Path
    host: str
    port: int
    source: Path
    memory_layers: MemoryLayerSettings | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        root = Path(__file__).resolve().parents[1]
        source = Path(path or os.getenv("MEMORIA_CONFIG", root / "config.toml"))
        if not source.exists():
            source = root / "config.example.toml"
        data = _read_with_parent(source.resolve())
        llm = data.get("llm", {})
        agent = data.get("agent", {})
        skills_section = agent.get("skills", {})
        memory_section = data.get("memory", {})
        memory = memory_section.get("embedding", {})
        retrieval = memory_section.get("retrieval", {})
        worker = memory_section.get("worker", {})
        server = data.get("server", {})
        security = server.get("security", {})
        observability = data.get("observability", {})
        storage = data.get("storage", {})
        tools_section = agent.get("tools", {})
        mcp_section = agent.get("mcp", {})
        drift_section = agent.get("drift", {})
        proactive_drift = data.get("proactive", {}).get("drift", {})

        def model(section: dict[str, Any]) -> ModelConfig:
            return ModelConfig(
                model=str(section.get("model", "")),
                api_key=_resolve(str(section.get("api_key", ""))),
                base_url=str(section.get("base_url", "")).rstrip("/"),
            )

        main = model(llm.get("main", {}))
        fast = model(llm.get("fast", {}))
        vector_backend = str(retrieval.get("vector_backend", "auto")).lower()
        if vector_backend not in {"auto", "sqlite-vec", "json"}:
            raise ValueError("memory.retrieval.vector_backend 必须是 auto、sqlite-vec 或 json")
        default_origins = ("http://localhost:5173", "http://127.0.0.1:5173")
        default_http_hosts = ("wttr.in", "api.open-meteo.com", "open-meteo.com")
        db = Path(str(storage.get("database", "data/memoria.db")))
        if not db.is_absolute():
            db = root / db
        skills_dir = Path(str(skills_section.get("directory", "skills")))
        if not skills_dir.is_absolute():
            skills_dir = root / skills_dir
        markdown_dir = Path(str(memory_section.get("markdown", {}).get("directory", "data/markdown")))
        if not markdown_dir.is_absolute():
            markdown_dir = root / markdown_dir
        mcp_config = Path(str(mcp_section.get("config_file", "data/mcp_servers.json")))
        if not mcp_config.is_absolute():
            mcp_config = root / mcp_config
        tool_search_enabled = bool(
            tools_section.get("search_enabled", tools_section.get("tool_search_enabled", True))
        )
        # Prefer [agent.drift]; fall back to legacy [proactive.drift].
        if not drift_section and proactive_drift:
            drift_section = proactive_drift
        min_interval_hours = float(drift_section.get("min_interval_hours", 3))
        interval_seconds = int(
            drift_section.get(
                "interval_seconds",
                max(60, int(min_interval_hours * 3600)),
            )
        )
        settings = cls(
            root=root,
            database=db,
            main=main,
            fast=fast,
            embedding=model(memory),
            system_prompt=str(agent.get("system_prompt", "你是 Memoria，一个拥有长期记忆的 AI 助手。")),
            max_tokens=int(agent.get("max_tokens", 4096)),
            max_iterations=int(agent.get("max_iterations", 8)),
            memory_window=int(agent.get("context", {}).get("memory_window", 30)),
            context_char_budget=int(agent.get("context", {}).get("char_budget", 60000)),
            request_timeout_seconds=float(llm.get("request_timeout_seconds", 90)),
            max_retries=int(llm.get("max_retries", 2)),
            vector_backend=vector_backend,
            vector_scan_limit=max(100, int(retrieval.get("vector_scan_limit", 2000))),
            memory_job_lease_seconds=max(30, int(worker.get("lease_seconds", 180))),
            memory_job_max_retries=max(1, int(worker.get("max_retries", 3))),
            memory_job_backoff_seconds=max(1, int(worker.get("backoff_seconds", 5))),
            api_token=_resolve(str(security.get("api_token", ""))),
            allowed_origins=_string_tuple(security.get("allowed_origins"), default_origins),
            rate_limit_per_minute=max(0, int(security.get("rate_limit_per_minute", 0))),
            max_request_bytes=max(1024, int(security.get("max_request_bytes", 1_048_576))),
            metrics_enabled=bool(observability.get("metrics_enabled", True)),
            skills_enabled=bool(skills_section.get("enabled", True)),
            skills_directory=skills_dir,
            skills_max_inject=max(0, int(skills_section.get("max_inject", 2))),
            http_allowed_hosts=_string_tuple(tools_section.get("http_allowed_hosts"), default_http_hosts),
            tool_search_enabled=tool_search_enabled,
            mcp_enabled=bool(mcp_section.get("enabled", True)),
            mcp_config_file=mcp_config,
            drift_enabled=bool(drift_section.get("enabled", False)),
            drift_min_idle_seconds=max(30, int(drift_section.get("min_idle_seconds", 300))),
            drift_interval_seconds=max(60, interval_seconds),
            drift_max_steps=max(1, min(40, int(drift_section.get("max_steps", 8)))),
            drift_daily_budget=max(1, int(drift_section.get("daily_budget", 6))),
            drift_quiet_hours=_int_tuple(drift_section.get("quiet_hours"), (0, 1, 2, 3, 4, 5, 6)),
            drift_allowed_skills=_string_tuple(
                drift_section.get("allowed_skills"),
                ("drift-digest", "memory-review"),
            ),
            drift_allow_write_tools=(
                ()
                if isinstance(drift_section.get("allow_write_tools"), (list, tuple))
                and len(drift_section.get("allow_write_tools")) == 0
                else _string_tuple(drift_section.get("allow_write_tools"), ("memorize",))
            ),
            drift_timezone=str(drift_section.get("timezone", "Asia/Shanghai")),
            markdown_enabled=bool(memory_section.get("markdown", {}).get("enabled", True)),
            markdown_directory=markdown_dir,
            host=str(server.get("host", "127.0.0.1")),
            port=int(server.get("port", 2237)),
            source=source,
        )
        # Import after ModelConfig is defined: memory's embedding module imports it.
        from .memory.layer_settings import MemoryLayerSettings

        settings.memory_layers = MemoryLayerSettings.from_dict(memory_section.get("layers", {}))
        from .models_config import apply_overrides

        return apply_overrides(settings)

    def public_dict(self) -> dict[str, Any]:
        def safe(value: ModelConfig) -> dict[str, Any]:
            return {"model": value.model, "base_url": value.base_url, "configured": bool(value.api_key)}
        return {
            "main": safe(self.main), "fast": safe(self.fast), "embedding": safe(self.embedding),
            "vector_backend": self.vector_backend, "auth_enabled": bool(self.api_token),
            "rate_limit_per_minute": self.rate_limit_per_minute, "config_source": str(self.source),
            "skills_enabled": self.skills_enabled, "skills_directory": str(self.skills_directory),
            "tool_search_enabled": self.tool_search_enabled,
            "mcp_enabled": self.mcp_enabled, "mcp_config_file": str(self.mcp_config_file),
            "drift_enabled": self.drift_enabled,
            "markdown_enabled": self.markdown_enabled, "markdown_directory": str(self.markdown_directory),
            "memory_layers": self.memory_layers.public_dict() if self.memory_layers else None,
            "setup_needed": not bool(self.main.api_key and self.main.model and self.main.base_url),
        }


def _read_with_parent(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    seen = seen or set()
    if path in seen:
        raise ValueError(f"配置 extends 循环引用: {path}")
    seen.add(path)
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    parent = data.pop("extends", None)
    if not parent:
        return data
    parent_path = Path(str(parent))
    if not parent_path.is_absolute():
        parent_path = path.parent / parent_path
    return _merge(_read_with_parent(parent_path.resolve(), seen), data)
