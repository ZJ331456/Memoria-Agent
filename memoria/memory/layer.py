from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .markdown import export_memories_markdown

DEFAULT_SELF = """# SELF

我是 Memoria，用户的长期 AI 伙伴。

## 行事原则
- 用与用户相同的语言回复
- 区分用户原话、检索记忆、工具结果与猜测
- 不主动保存凭据或敏感秘密
- 重要偏好变化时更新记忆，而不是沉默覆盖

## 当前关注
- 帮助用户保持连续上下文
- 在确认后整理和校正长期记忆
"""

DEFAULT_PENDING = """# PENDING

Consolidation 待归档候选会追加到这里。成功写入结构化记忆后会移到下方已处理区。

## Open

## Done
"""

_PENDING_ITEM = re.compile(
    r"^- \[(?P<status>open|done)\] `(?P<source>[^`]+)` \((?P<kind>[^)]+)\) (?P<body>.+)$"
)


@dataclass
class MarkdownMemoryLayer:
    """Human-readable markdown layer living beside the SQLite memory store."""

    root: Path
    enabled: bool = True
    _lock: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        if self.enabled:
            self.ensure()

    @property
    def memory_path(self) -> Path:
        return self.root / "MEMORY.md"

    @property
    def self_path(self) -> Path:
        return self.root / "SELF.md"

    @property
    def pending_path(self) -> Path:
        return self.root / "PENDING.md"

    def ensure(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.self_path.exists():
                self.self_path.write_text(DEFAULT_SELF, encoding="utf-8")
            if not self.pending_path.exists():
                self.pending_path.write_text(DEFAULT_PENDING, encoding="utf-8")
            if not self.memory_path.exists():
                self.memory_path.write_text("# MEMORY\n\n_暂无长期记忆。_\n", encoding="utf-8")

    def sync_memory(self, memories: list[dict[str, Any]]) -> str:
        if not self.enabled:
            return ""
        content = export_memories_markdown(memories, title="MEMORY")
        with self._lock:
            self.ensure()
            self.memory_path.write_text(content, encoding="utf-8")
        return content

    def read(self, name: str) -> str:
        path = self._path(name)
        with self._lock:
            self.ensure()
            return path.read_text(encoding="utf-8")

    def write(self, name: str, content: str) -> str:
        if name == "MEMORY":
            raise ValueError("MEMORY.md 由结构化记忆自动投影，请通过记忆 API 修改")
        path = self._path(name)
        text = content.strip() + "\n"
        with self._lock:
            self.ensure()
            path.write_text(text, encoding="utf-8")
        return text

    def self_excerpt(self, max_chars: int = 1800) -> str:
        text = self.read("SELF").strip()
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[SELF.md 已截断]"
        return text

    def append_pending(self, *, source_ref: str, content: str, kind: str = "fact") -> None:
        if not self.enabled:
            return
        line = f"- [open] `{source_ref}` ({kind}) {content.strip().replace(chr(10), ' ')}"
        with self._lock:
            self.ensure()
            current = self.pending_path.read_text(encoding="utf-8")
            if line in current:
                return
            if "## Open" in current:
                current = current.replace("## Open", "## Open\n" + line, 1)
            else:
                current = current.rstrip() + "\n\n## Open\n" + line + "\n"
            self.pending_path.write_text(current if current.endswith("\n") else current + "\n", encoding="utf-8")

    def complete_pending(self, *, source_ref: str, content: str, kind: str = "fact") -> None:
        if not self.enabled:
            return
        needle_body = content.strip().replace("\n", " ")
        with self._lock:
            self.ensure()
            lines = self.pending_path.read_text(encoding="utf-8").splitlines()
            open_lines: list[str] = []
            done_lines: list[str] = []
            mode = "header"
            header: list[str] = []
            moved = False
            for line in lines:
                if line.strip() == "## Open":
                    mode = "open"
                    continue
                if line.strip() == "## Done":
                    mode = "done"
                    continue
                if mode == "header":
                    header.append(line)
                    continue
                match = _PENDING_ITEM.match(line.strip())
                if (
                    match
                    and match.group("source") == source_ref
                    and match.group("body").strip() == needle_body
                ):
                    done_lines.append(f"- [done] `{source_ref}` ({match.group('kind')}) {needle_body}")
                    moved = True
                    continue
                if mode == "open" and line.strip():
                    open_lines.append(line)
                elif mode == "done" and line.strip():
                    done_lines.append(line)
            if not moved:
                done_lines.insert(0, f"- [done] `{source_ref}` ({kind}) {needle_body}")
            body = "\n".join(header).rstrip()
            if "# PENDING" not in body:
                body = "# PENDING\n\nConsolidation 待归档候选会追加到这里。"
            text = body + "\n\n## Open\n"
            text += ("\n".join(open_lines) + "\n") if open_lines else "\n"
            text += "\n## Done\n"
            text += ("\n".join(done_lines[:100]) + "\n") if done_lines else "\n"
            self.pending_path.write_text(text, encoding="utf-8")

    def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        with self._lock:
            self.ensure()
            pending = self.pending_path.read_text(encoding="utf-8")
            open_count = len([line for line in pending.splitlines() if line.startswith("- [open]")])
            return {
                "enabled": True,
                "directory": str(self.root),
                "files": {
                    "MEMORY.md": self.memory_path.stat().st_size,
                    "SELF.md": self.self_path.stat().st_size,
                    "PENDING.md": self.pending_path.stat().st_size,
                },
                "pending_open": open_count,
            }

    def _path(self, name: str) -> Path:
        mapping = {
            "MEMORY": self.memory_path,
            "SELF": self.self_path,
            "PENDING": self.pending_path,
            "memory": self.memory_path,
            "self": self.self_path,
            "pending": self.pending_path,
        }
        path = mapping.get(name)
        if not path:
            raise KeyError(f"未知 markdown 层: {name}")
        return path
