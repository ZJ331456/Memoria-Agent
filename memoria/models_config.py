from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import httpx

from .config import ModelConfig, Settings, SlotRole, _merge


SLOT_PATHS = {
    "main": ("llm", "main"),
    "fast": ("llm", "fast"),
    "embedding": ("memory", "embedding"),
}


def override_path(settings: Settings) -> Path:
    return settings.database.parent / "models.override.toml"


def load_model_overrides(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def apply_overrides(settings: Settings, data: dict[str, Any] | None = None) -> Settings:
    payload = data if data is not None else load_model_overrides(override_path(settings))
    if not payload:
        return settings
    for slot, keys in SLOT_PATHS.items():
        section = payload
        for key in keys:
            if not isinstance(section, dict):
                section = {}
                break
            section = section.get(key, {})
        if not isinstance(section, dict) or not section:
            continue
        current = getattr(settings, slot)
        api_key = str(section.get("api_key", "")).strip()
        setattr(
            settings,
            slot,
            ModelConfig(
                model=str(section.get("model", current.model) or current.model),
                api_key=api_key if api_key else current.api_key,
                base_url=str(section.get("base_url", current.base_url) or current.base_url).rstrip("/"),
            ),
        )
    return settings


def public_models(settings: Settings) -> dict[str, Any]:
    def safe(value: ModelConfig, *, role: SlotRole) -> dict[str, Any]:
        from .llm.backends.resolve import backend_public_label

        payload = {
            "model": value.model,
            "base_url": value.base_url,
            "configured": value.is_ready(role=role),
            "api_key_set": bool(value.api_key),
            "local": value.is_local_process() or (role == "llm" and value.is_local_http()),
        }
        if role == "llm":
            payload["backend"] = backend_public_label(value) if value.base_url else ""
        return payload

    return {
        "main": safe(settings.main, role="llm"),
        "fast": safe(settings.fast, role="llm"),
        "embedding": safe(settings.embedding, role="embedding"),
        "setup_needed": not settings.main.is_ready(role="llm"),
        "override_path": str(override_path(settings)),
    }


def save_model_overrides(settings: Settings, updates: dict[str, dict[str, str]]) -> dict[str, Any]:
    path = override_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_model_overrides(path)
    patch: dict[str, Any] = {}
    for slot, values in updates.items():
        if slot not in SLOT_PATHS or not isinstance(values, dict):
            continue
        section: dict[str, str] = {}
        if values.get("model") is not None:
            section["model"] = str(values.get("model", "")).strip()
        if values.get("base_url") is not None:
            section["base_url"] = str(values.get("base_url", "")).strip().rstrip("/")
        api_key = values.get("api_key")
        if api_key is not None and str(api_key).strip():
            section["api_key"] = str(api_key).strip()
        if not section:
            continue
        cursor = patch
        keys = SLOT_PATHS[slot]
        for key in keys[:-1]:
            cursor = cursor.setdefault(key, {})
        cursor[keys[-1]] = section
        current = getattr(settings, slot)
        setattr(
            settings,
            slot,
            ModelConfig(
                model=section.get("model", current.model) or current.model,
                api_key=section["api_key"] if "api_key" in section else current.api_key,
                base_url=(section.get("base_url", current.base_url) or current.base_url).rstrip("/"),
            ),
        )
    merged = _merge(existing, patch)
    path.write_text(_dump_toml(merged), encoding="utf-8")
    return public_models(settings)


def _dump_toml(data: dict[str, Any], prefix: str = "") -> str:
    lines: list[str] = []
    tables: list[tuple[str, dict[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            path = f"{prefix}.{key}" if prefix else key
            # leaf model tables have scalar values only
            if value and all(not isinstance(item, dict) for item in value.values()):
                lines.append(f"[{path}]")
                for nested_key, nested_value in value.items():
                    lines.append(f'{nested_key} = "{_escape(str(nested_value))}"')
                lines.append("")
            else:
                tables.append((path, value))
        else:
            target = lines if not prefix else lines
            target.append(f'{key} = "{_escape(str(value))}"')
    for path, value in tables:
        nested = _dump_toml(value, path)
        if nested.strip():
            lines.append(nested.rstrip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def test_model_slot(settings: Settings, slot: str) -> dict[str, Any]:
    if slot not in {"main", "fast", "embedding"}:
        raise ValueError("slot 必须是 main、fast 或 embedding")
    config: ModelConfig = getattr(settings, slot)
    role: SlotRole = "embedding" if slot == "embedding" else "llm"
    if slot == "embedding" and config.is_local_process():
        from .memory.embedding import EmbeddingClient, EmbeddingError
        client = EmbeddingClient(config, min(settings.request_timeout_seconds, 60), max_retries=0)
        try:
            vector = await client.embed_query("用户喜欢喝茶")
            if not vector:
                raise EmbeddingError("本地模型未配置或未返回向量")
            return {"ok": True, "slot": slot, "message": "本地 Embedding 编码正常",
                    "dimension": len(vector), "namespace": client.namespace}
        except EmbeddingError as exc:
            return {"ok": False, "slot": slot, "message": str(exc)}
    if role == "llm" and config.is_local_process():
        from .llm import LLMClient, ProviderError
        try:
            result = await LLMClient(settings).chat(
                [{"role": "user", "content": "ping"}],
                model=config,
                max_tokens=8,
            )
            return {
                "ok": True,
                "slot": slot,
                "message": "本地 LLM 推理正常",
                "preview": (result.content or "")[:80],
                "device": config.base_url.removeprefix("local://"),
            }
        except (ProviderError, RuntimeError, OSError, ImportError) as exc:
            return {"ok": False, "slot": slot, "message": str(exc)}
    if not config.is_ready(role=role):
        if role == "llm" and config.model and config.base_url and not config.api_key:
            return {"ok": False, "slot": slot, "message": "远程模型需要 API Key；本地可用 local://cuda 或 localhost OpenAI 兼容服务"}
        return {"ok": False, "slot": slot, "message": "模型未完整配置（需要 model、base_url；远程还需 api_key）"}
    headers = config.auth_headers()
    timeout = httpx.Timeout(min(settings.request_timeout_seconds, 30))
    async with httpx.AsyncClient(timeout=timeout) as client:
        if slot == "embedding":
            response = await client.post(
                f"{config.base_url}/embeddings",
                headers=headers,
                json={"model": config.model, "input": ["memoria setup ping"]},
            )
        else:
            response = await client.post(
                f"{config.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": config.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 8,
                },
            )
    if response.status_code >= 400:
        detail = response.text[:300]
        return {"ok": False, "slot": slot, "status_code": response.status_code, "message": detail}
    message = "本地 OpenAI 兼容服务连通正常" if config.is_local_http() else "连通性正常"
    return {"ok": True, "slot": slot, "status_code": response.status_code, "message": message}
