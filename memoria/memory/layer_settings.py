"""Validated budgets and retention settings for the five memory layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping


_INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "semantic_chars": (0, 20_000),
    "episodic_chars": (0, 20_000),
    "procedural_chars": (0, 20_000),
    "summary_chars": (0, 20_000),
    "catalog_chars": (0, 20_000),
    "self_chars": (0, 20_000),
    "context_chars": (4_000, 200_000),
    "episode_ttl_days": (1, 3_650),
    "episode_top_k": (0, 20),
    "episode_max_active": (1, 100_000),
    "maintenance_interval_seconds": (60, 86_400),
    "retrieval_max_calls": (1, 100),
    "retrieval_max_chars": (1_000, 200_000),
}


@dataclass(frozen=True, slots=True)
class MemoryLayerSettings:
    """Flat ``[memory.layers]`` values with deliberately bounded resource use."""

    enabled: bool = True
    episodic_enabled: bool = True
    semantic_chars: int = 3_000
    episodic_chars: int = 1_800
    procedural_chars: int = 2_400
    summary_chars: int = 2_000
    catalog_chars: int = 1_200
    self_chars: int = 1_200
    context_chars: int = 12_000
    episode_ttl_days: int = 90
    episode_top_k: int = 3
    episode_max_active: int = 1_000
    maintenance_interval_seconds: int = 3_600
    retrieval_max_calls: int = 6
    retrieval_max_chars: int = 12_000

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "MemoryLayerSettings":
        if data is None:
            data = {}
        if not isinstance(data, Mapping):
            raise ValueError("memory.layers 必须是键值表")
        known = {field.name for field in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"memory.layers 包含未知字段: {', '.join(sorted(map(str, unknown)))}")
        values: dict[str, Any] = {}
        for name, value in data.items():
            if name in {"enabled", "episodic_enabled"}:
                if type(value) is not bool:
                    raise ValueError(f"memory.layers.{name} 必须是布尔值")
            else:
                if type(value) is not int:
                    raise ValueError(f"memory.layers.{name} 必须是整数")
                low, high = _INTEGER_RANGES[name]
                if not low <= value <= high:
                    raise ValueError(f"memory.layers.{name} 必须在 {low}–{high} 之间")
            values[name] = value
        return cls(**values)

    def public_dict(self) -> dict[str, bool | int]:
        return asdict(self)
