from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    host: str
    port: int
    source: Path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        root = Path(__file__).resolve().parents[1]
        source = Path(path or os.getenv("MEMORIA_CONFIG", root / "config.toml"))
        if not source.exists():
            source = root / "config.example.toml"
        data = _read_with_parent(source.resolve())
        llm = data.get("llm", {})
        agent = data.get("agent", {})
        memory_section = data.get("memory", {})
        memory = memory_section.get("embedding", {})
        retrieval = memory_section.get("retrieval", {})
        worker = memory_section.get("worker", {})
        server = data.get("server", {})
        security = server.get("security", {})
        observability = data.get("observability", {})
        storage = data.get("storage", {})

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
        db = Path(str(storage.get("database", "data/memoria.db")))
        if not db.is_absolute():
            db = root / db
        return cls(
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
            host=str(server.get("host", "127.0.0.1")),
            port=int(server.get("port", 2237)),
            source=source,
        )

    def public_dict(self) -> dict[str, Any]:
        def safe(value: ModelConfig) -> dict[str, Any]:
            return {"model": value.model, "base_url": value.base_url, "configured": bool(value.api_key)}
        return {
            "main": safe(self.main), "fast": safe(self.fast), "embedding": safe(self.embedding),
            "vector_backend": self.vector_backend, "auth_enabled": bool(self.api_token),
            "rate_limit_per_minute": self.rate_limit_per_minute, "config_source": str(self.source),
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
