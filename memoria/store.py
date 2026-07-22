from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact',
                    importance INTEGER NOT NULL DEFAULT 3, source TEXT NOT NULL DEFAULT 'manual',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at DESC);
                CREATE TABLE IF NOT EXISTS turn_traces (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL,
                    steps INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0,
                    memories_json TEXT NOT NULL DEFAULT '[]', tools_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_traces_session ON turn_traces(session_id, created_at DESC);
            """)
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def create_session(self, title: str = "新对话") -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "title": title.strip() or "新对话", "created_at": now(), "updated_at": now()}
        with self.lock:
            self.db.execute("INSERT INTO sessions VALUES (:id,:title,:created_at,:updated_at)", item)
            self.db.commit()
        return item

    def sessions(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("""SELECT s.*, COUNT(m.id) message_count FROM sessions s
                LEFT JOIN messages m ON m.session_id=s.id GROUP BY s.id ORDER BY s.updated_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def session(self, session_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?", (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "session_id": session_id, "role": role, "content": content, "created_at": now()}
        with self.lock:
            self.db.execute("INSERT INTO messages VALUES (:id,:session_id,:role,:content,:created_at)", item)
            self.db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (item["created_at"], session_id))
            self.db.commit()
        return item

    def rename_session(self, session_id: str, title: str) -> None:
        with self.lock:
            self.db.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title[:80], now(), session_id))
            self.db.commit()

    def delete_session(self, session_id: str) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self.db.commit()
        return cur.rowcount > 0

    def memories(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            if query:
                rows = self.db.execute("SELECT * FROM memories WHERE content LIKE ? ORDER BY importance DESC, updated_at DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM memories ORDER BY importance DESC, updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def add_memory(self, content: str, kind: str = "fact", importance: int = 3, source: str = "manual") -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "content": content.strip(), "kind": kind, "importance": max(1, min(5, importance)), "source": source, "created_at": now(), "updated_at": now()}
        with self.lock:
            self.db.execute("INSERT INTO memories VALUES (:id,:content,:kind,:importance,:source,:created_at,:updated_at)", item)
            self.db.commit()
        return item

    def update_memory(self, memory_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        current = next((m for m in self.memories(limit=1000) if m["id"] == memory_id), None)
        if not current:
            return None
        current.update({k: v for k, v in data.items() if k in {"content", "kind", "importance"} and v is not None})
        current["importance"] = max(1, min(5, int(current["importance"])))
        current["updated_at"] = now()
        with self.lock:
            self.db.execute("UPDATE memories SET content=?,kind=?,importance=?,updated_at=? WHERE id=?", (current["content"], current["kind"], current["importance"], current["updated_at"], memory_id))
            self.db.commit()
        return current

    def delete_memory(self, memory_id: str) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            self.db.commit()
        return cur.rowcount > 0

    def overview(self) -> dict[str, int]:
        with self.lock:
            sessions = self.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            memories = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            traces = self.db.execute("SELECT COUNT(*) FROM turn_traces").fetchone()[0]
        return {"sessions": sessions, "messages": messages, "memories": memories, "traces": traces}

    def search_messages(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT m.*, s.title session_title FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.content LIKE ? ORDER BY m.created_at DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
        return [dict(row) for row in rows]

    def add_trace(self, session_id: str, status: str, steps: int, duration_ms: int, memories: list[dict], tools: list[dict], error: str | None = None) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "session_id": session_id, "status": status, "steps": steps, "duration_ms": duration_ms, "memories_json": json.dumps(memories, ensure_ascii=False), "tools_json": json.dumps(tools, ensure_ascii=False), "error": error, "created_at": now()}
        with self.lock:
            self.db.execute("INSERT INTO turn_traces VALUES (:id,:session_id,:status,:steps,:duration_ms,:memories_json,:tools_json,:error,:created_at)", item)
            self.db.commit()
        return self._trace(item)

    def traces(self, session_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            if session_id:
                rows = self.db.execute("SELECT * FROM turn_traces WHERE session_id=? ORDER BY created_at DESC LIMIT ?", (session_id, limit)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM turn_traces ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._trace(dict(row)) for row in rows]

    @staticmethod
    def _trace(item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        result["memories"] = json.loads(result.pop("memories_json", "[]"))
        result["tools"] = json.loads(result.pop("tools_json", "[]"))
        return result
