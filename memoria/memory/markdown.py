from __future__ import annotations

from collections import defaultdict
from typing import Any


KIND_TITLES = {
    "profile": "Profile",
    "preference": "Preferences",
    "goal": "Goals",
    "procedure": "Procedures",
    "fact": "Facts",
}


def export_memories_markdown(memories: list[dict[str, Any]], *, title: str = "MEMORY") -> str:
    """Render active memories into a human-readable Markdown layer."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for memory in memories:
        if memory.get("status", "active") != "active":
            continue
        groups[str(memory.get("kind") or "fact")].append(memory)
    lines = [f"# {title}", ""]
    if not groups:
        lines.append("_暂无长期记忆。_")
        lines.append("")
        return "\n".join(lines)
    for kind in ("profile", "preference", "goal", "procedure", "fact"):
        items = groups.pop(kind, [])
        if not items:
            continue
        lines.append(f"## {KIND_TITLES.get(kind, kind.title())}")
        lines.append("")
        for item in sorted(items, key=lambda row: (-int(row.get("importance") or 0), str(row.get("updated_at") or ""))):
            importance = int(item.get("importance") or 3)
            reinforcement = int(item.get("reinforcement") or 1)
            memory_id = item.get("id") or ""
            content = str(item.get("content") or "").strip().replace("\n", " ")
            lines.append(f"- ({importance}/5, x{reinforcement}) `{memory_id}` {content}")
        lines.append("")
    for kind, items in sorted(groups.items()):
        lines.append(f"## {kind.title()}")
        lines.append("")
        for item in items:
            lines.append(f"- `{item.get('id', '')}` {str(item.get('content') or '').strip()}")
        lines.append("")
    return "\n".join(lines)
