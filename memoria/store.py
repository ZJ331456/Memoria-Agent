from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .vector_index import SQLiteVecIndex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class Store:
    def __init__(self, path: Path, vector_backend: str = "auto"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                PRAGMA busy_timeout=5000;
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
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, embedding TEXT,
                    status TEXT NOT NULL DEFAULT 'active', reinforcement INTEGER NOT NULL DEFAULT 1,
                    supersedes_id TEXT, last_reinforced_at TEXT, source_ref TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at DESC);
                CREATE TABLE IF NOT EXISTS memory_replacements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, old_memory_id TEXT NOT NULL,
                    new_memory_id TEXT NOT NULL, old_content TEXT NOT NULL,
                    new_content TEXT NOT NULL, relation TEXT NOT NULL DEFAULT 'supersede',
                    reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_replacements_old ON memory_replacements(old_memory_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_replacements_new ON memory_replacements(new_memory_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS memory_operations (
                    id TEXT PRIMARY KEY, source_ref TEXT NOT NULL, memory_id TEXT NOT NULL,
                    action TEXT NOT NULL, previous_id TEXT, created_at TEXT NOT NULL, undone_at TEXT,
                    UNIQUE(source_ref,memory_id,action)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_operations_source ON memory_operations(source_ref, undone_at);
                CREATE TABLE IF NOT EXISTS memory_jobs (
                    id TEXT PRIMARY KEY, source_ref TEXT NOT NULL UNIQUE, user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT, available_at TEXT NOT NULL, lease_owner TEXT, lease_expires_at TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_jobs_status ON memory_jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS memory_reviews (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_ref TEXT NOT NULL,
                    ordinal INTEGER NOT NULL, content TEXT NOT NULL, kind TEXT NOT NULL,
                    importance INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    applied_memory_id TEXT, applied_action TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(job_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_reviews_status ON memory_reviews(status, created_at);
                CREATE TABLE IF NOT EXISTS turn_traces (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL,
                    steps INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0,
                    memories_json TEXT NOT NULL DEFAULT '[]', tools_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}', error TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_traces_session ON turn_traces(session_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS runtime_metrics (
                    key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS session_compactions (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    summary TEXT NOT NULL,
                    covered_message_ids TEXT NOT NULL DEFAULT '[]',
                    cursor_message_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drift_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    skill TEXT NOT NULL,
                    trigger TEXT NOT NULL DEFAULT 'scheduler',
                    status TEXT NOT NULL,
                    steps INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    trace_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_drift_runs_created ON drift_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_drift_runs_skill ON drift_runs(skill, status, finished_at DESC);
            """)
            self.db.commit()
            session_columns = {row[1] for row in self.db.execute("PRAGMA table_info(sessions)").fetchall()}
            if "interrupt_note" not in session_columns:
                self.db.execute("ALTER TABLE sessions ADD COLUMN interrupt_note TEXT")
            memory_columns = {row[1] for row in self.db.execute("PRAGMA table_info(memories)").fetchall()}
            migrations = {
                "embedding": "ALTER TABLE memories ADD COLUMN embedding TEXT",
                "status": "ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
                "reinforcement": "ALTER TABLE memories ADD COLUMN reinforcement INTEGER NOT NULL DEFAULT 1",
                "supersedes_id": "ALTER TABLE memories ADD COLUMN supersedes_id TEXT",
                "last_reinforced_at": "ALTER TABLE memories ADD COLUMN last_reinforced_at TEXT",
                "source_ref": "ALTER TABLE memories ADD COLUMN source_ref TEXT",
            }
            for column, statement in migrations.items():
                if column not in memory_columns:
                    self.db.execute(statement)
            trace_columns = {row[1] for row in self.db.execute("PRAGMA table_info(turn_traces)").fetchall()}
            if "metadata_json" not in trace_columns:
                self.db.execute("ALTER TABLE turn_traces ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
            job_columns = {row[1] for row in self.db.execute("PRAGMA table_info(memory_jobs)").fetchall()}
            job_migrations = {
                "available_at": "ALTER TABLE memory_jobs ADD COLUMN available_at TEXT",
                "lease_owner": "ALTER TABLE memory_jobs ADD COLUMN lease_owner TEXT",
                "lease_expires_at": "ALTER TABLE memory_jobs ADD COLUMN lease_expires_at TEXT",
            }
            for column, statement in job_migrations.items():
                if column not in job_columns:
                    self.db.execute(statement)
            self.db.execute("UPDATE memory_jobs SET available_at=COALESCE(available_at,created_at)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_memory_jobs_available ON memory_jobs(status, available_at, lease_expires_at)")
            self.db.execute("UPDATE memory_reviews SET status='pending' WHERE status='applying'")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, updated_at DESC)")
            for _column, _statement in {
                "valid_at": "ALTER TABLE memories ADD COLUMN valid_at TEXT",
                "invalid_at": "ALTER TABLE memories ADD COLUMN invalid_at TEXT",
                "attributes_json": "ALTER TABLE memories ADD COLUMN attributes_json TEXT NOT NULL DEFAULT '{}'",
                "entities_json": "ALTER TABLE memories ADD COLUMN entities_json TEXT NOT NULL DEFAULT '[]'",
                "provenance_json": "ALTER TABLE memories ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
            }.items():
                try:
                    self.db.execute(_statement)
                except sqlite3.OperationalError:
                    pass
            self.db.execute("""CREATE TABLE IF NOT EXISTS memory_links (
                id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'related', weight REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL, UNIQUE(from_id,to_id,relation))""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_from ON memory_links(from_id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_to ON memory_links(to_id)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS memory_evolutions (
                id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, summary TEXT NOT NULL,
                created_at TEXT NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_memory_evolutions_mem ON memory_evolutions(memory_id, created_at DESC)")
            self._init_fts()
            self.vector_index = SQLiteVecIndex(self.db, vector_backend)
            self._bootstrap_vector_index()
            self.db.commit()

    def _bootstrap_vector_index(self) -> None:
        rows = [dict(row) for row in self.db.execute("SELECT id,embedding FROM memories WHERE embedding IS NOT NULL ORDER BY updated_at DESC")]
        expected_dimension = None
        valid = []
        for item in rows:
            try:
                vector = json.loads(item["embedding"])
            except (json.JSONDecodeError, TypeError):
                vector = None
            if not isinstance(vector, list) or not vector:
                self.db.execute("UPDATE memories SET embedding=NULL WHERE id=?", (item["id"],))
                continue
            expected_dimension = expected_dimension or len(vector)
            if len(vector) != expected_dimension:
                self.db.execute("UPDATE memories SET embedding=NULL WHERE id=?", (item["id"],))
                continue
            valid.append(item)
        self.vector_index.bootstrap(valid)

    def _prepare_vector_dimension(self, vector: list[float], keep_id: str) -> None:
        if self.vector_index.enabled and self.vector_index.dimension not in {None, len(vector)}:
            self.db.execute("UPDATE memories SET embedding=NULL WHERE id<>?", (keep_id,))

    def _init_fts(self) -> None:
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id UNINDEXED, content, tokenize='trigram')")
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(id UNINDEXED, content, tokenize='trigram')")
        except sqlite3.OperationalError:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id UNINDEXED, content)")
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(id UNINDEXED, content)")
        definition = self.db.execute("SELECT sql FROM sqlite_master WHERE name='memories_fts'").fetchone()
        self._memory_fts_trigram = bool(definition and "trigram" in definition[0].lower())
        self.db.execute("INSERT INTO memories_fts(id,content) SELECT id,content FROM memories WHERE id NOT IN (SELECT id FROM memories_fts)")
        self.db.execute("INSERT INTO messages_fts(id,content) SELECT id,content FROM messages WHERE id NOT IN (SELECT id FROM messages_fts)")

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def create_session(self, title: str = "新对话") -> dict[str, Any]:
        item = {
            "id": uuid.uuid4().hex,
            "title": title.strip() or "新对话",
            "created_at": now(),
            "updated_at": now(),
            "interrupt_note": None,
        }
        with self.lock:
            self.db.execute(
                "INSERT INTO sessions (id,title,created_at,updated_at,interrupt_note) VALUES (:id,:title,:created_at,:updated_at,:interrupt_note)",
                item,
            )
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

    def messages(self, session_id: str, limit: int = 100, anchor_id: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            if anchor_id:
                anchor = self.db.execute("SELECT rowid FROM messages WHERE id=? AND session_id=?", (anchor_id, session_id)).fetchone()
                if not anchor:
                    return []
                before = self.db.execute("SELECT * FROM messages WHERE session_id=? AND rowid<=? ORDER BY rowid DESC LIMIT ?", (session_id, anchor[0], max(1, limit // 2))).fetchall()
                after = self.db.execute("SELECT * FROM messages WHERE session_id=? AND rowid>? ORDER BY rowid ASC LIMIT ?", (session_id, anchor[0], limit - len(before))).fetchall()
                return [dict(row) for row in [*reversed(before), *after]]
            rows = self.db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?", (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def message_source(self, message_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("""SELECT m.id AS message_id,m.session_id,m.role,m.content,m.created_at,
                s.title AS session_title FROM messages m JOIN sessions s ON s.id=m.session_id
                WHERE m.id=?""", (message_id,)).fetchone()
        return dict(row) if row else None

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "session_id": session_id, "role": role, "content": content, "created_at": now()}
        with self.lock:
            self.db.execute("INSERT INTO messages VALUES (:id,:session_id,:role,:content,:created_at)", item)
            self.db.execute("INSERT INTO messages_fts(id,content) VALUES (?,?)", (item["id"], item["content"]))
            self.db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (item["created_at"], session_id))
            self.db.commit()
        return item

    def rename_session(self, session_id: str, title: str) -> None:
        with self.lock:
            self.db.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title[:80], now(), session_id))
            self.db.commit()

    def set_interrupt_note(self, session_id: str, note: str | None) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE sessions SET interrupt_note=?, updated_at=? WHERE id=?",
                (note, now(), session_id),
            )
            self.db.commit()

    def clear_interrupt_note(self, session_id: str) -> None:
        self.set_interrupt_note(session_id, None)

    def session_compaction(self, session_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM session_compactions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["covered_message_ids"] = json.loads(item.get("covered_message_ids") or "[]")
        except json.JSONDecodeError:
            item["covered_message_ids"] = []
        return item

    def upsert_session_compaction(
        self,
        session_id: str,
        summary: str,
        covered_message_ids: list[str],
        cursor_message_id: str | None,
    ) -> dict[str, Any]:
        item = {
            "session_id": session_id,
            "summary": summary,
            "covered_message_ids": json.dumps(list(covered_message_ids), ensure_ascii=False),
            "cursor_message_id": cursor_message_id,
            "updated_at": now(),
        }
        with self.lock:
            self.db.execute(
                """INSERT INTO session_compactions (session_id,summary,covered_message_ids,cursor_message_id,updated_at)
                   VALUES (:session_id,:summary,:covered_message_ids,:cursor_message_id,:updated_at)
                   ON CONFLICT(session_id) DO UPDATE SET
                     summary=excluded.summary,
                     covered_message_ids=excluded.covered_message_ids,
                     cursor_message_id=excluded.cursor_message_id,
                     updated_at=excluded.updated_at""",
                item,
            )
            self.db.commit()
        return self.session_compaction(session_id) or item

    def delete_session(self, session_id: str) -> bool:
        with self.lock:
            message_ids = [row[0] for row in self.db.execute("SELECT id FROM messages WHERE session_id=?", (session_id,)).fetchall()]
            for message_id in message_ids:
                self.db.execute("DELETE FROM messages_fts WHERE id=?", (message_id,))
            cur = self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self.db.commit()
        return cur.rowcount > 0

    def memories(self, query: str = "", limit: int = 100, status: str = "active") -> list[dict[str, Any]]:
        status = status if status in {"active", "superseded", "all"} else "active"
        clauses, params = [], []
        if status != "all":
            clauses.append("status=?")
            params.append(status)
        if query:
            clauses.append("content LIKE ?")
            params.append(f"%{query}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM memories{where} ORDER BY importance DESC, reinforcement DESC, updated_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._memory(dict(row)) for row in rows]

    def memory(self, memory_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._memory(dict(row)) if row else None

    def add_memory(self, content: str, kind: str = "fact", importance: int = 3, source: str = "manual", embedding: list[float] | None = None, supersedes_id: str | None = None, reason: str = "", source_ref: str | None = None) -> dict[str, Any]:
        timestamp = now()
        item = {
            "id": uuid.uuid4().hex, "content": content.strip(), "kind": kind,
            "importance": max(1, min(5, importance)), "source": source,
            "created_at": timestamp, "updated_at": timestamp,
            "embedding": json.dumps(embedding) if embedding else None,
            "status": "active", "reinforcement": 1,
            "supersedes_id": supersedes_id, "last_reinforced_at": None,
            "source_ref": source_ref,
        }
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                previous = None
                if supersedes_id:
                    previous = self.db.execute("SELECT * FROM memories WHERE id=? AND status='active' AND kind=?", (supersedes_id, kind)).fetchone()
                    if previous is None:
                        item["supersedes_id"] = None
                self.db.execute("""INSERT INTO memories
                    (id,content,kind,importance,source,created_at,updated_at,embedding,status,reinforcement,supersedes_id,last_reinforced_at,source_ref)
                    VALUES (:id,:content,:kind,:importance,:source,:created_at,:updated_at,:embedding,:status,:reinforcement,:supersedes_id,:last_reinforced_at,:source_ref)""", item)
                self.db.execute("INSERT INTO memories_fts(id,content) VALUES (?,?)", (item["id"], item["content"]))
                if embedding:
                    self._prepare_vector_dimension(embedding, item["id"])
                    self.vector_index.upsert(item["id"], embedding)
                if previous is not None:
                    self.db.execute("UPDATE memories SET status='superseded',updated_at=? WHERE id=?", (timestamp, supersedes_id))
                    self.db.execute("""INSERT INTO memory_replacements
                        (old_memory_id,new_memory_id,old_content,new_content,relation,reason,created_at)
                        VALUES (?,?,?,?,?,?,?)""", (supersedes_id, item["id"], previous["content"], item["content"], "supersede", reason[:500], timestamp))
                if source_ref:
                    self.db.execute("""INSERT OR IGNORE INTO memory_operations
                        (id,source_ref,memory_id,action,previous_id,created_at,undone_at) VALUES (?,?,?,?,?,?,NULL)""",
                        (uuid.uuid4().hex, source_ref, item["id"], "supersede" if previous is not None else "create", supersedes_id if previous is not None else None, timestamp))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return self._memory(item)

    def reinforce_memory(self, memory_id: str, source_ref: str | None = None) -> dict[str, Any] | None:
        timestamp = now()
        with self.lock:
            cur = self.db.execute("""UPDATE memories SET reinforcement=reinforcement+1,
                last_reinforced_at=?,updated_at=? WHERE id=? AND status='active'""", (timestamp, timestamp, memory_id))
            if cur.rowcount and source_ref:
                self.db.execute("""INSERT OR IGNORE INTO memory_operations
                    (id,source_ref,memory_id,action,previous_id,created_at,undone_at) VALUES (?,?,?,'reinforce',NULL,?,NULL)""",
                    (uuid.uuid4().hex, source_ref, memory_id, timestamp))
            self.db.commit()
        return self.memory(memory_id) if cur.rowcount else None

    def has_memory_operation(self, source_ref: str, memory_id: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT 1 FROM memory_operations WHERE source_ref=? AND memory_id=? LIMIT 1", (source_ref, memory_id)).fetchone()
        return row is not None

    def memory_history(self, memory_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("""SELECT * FROM memory_replacements
                WHERE old_memory_id=? OR new_memory_id=? ORDER BY created_at DESC""", (memory_id, memory_id)).fetchall()
        return [dict(row) for row in rows]

    def memory_timeline(self, memory_id: str) -> list[dict[str, Any]]:
        """Return the connected replacement chain, oldest version first."""
        with self.lock:
            rows = self.db.execute("""WITH RECURSIVE chain(id) AS (
                    SELECT id FROM memories WHERE id=?
                    UNION
                    SELECT r.old_memory_id FROM memory_replacements r JOIN chain c ON r.new_memory_id=c.id
                    UNION
                    SELECT r.new_memory_id FROM memory_replacements r JOIN chain c ON r.old_memory_id=c.id
                )
                SELECT m.*, (
                    SELECT r.reason FROM memory_replacements r WHERE r.new_memory_id=m.id
                    ORDER BY r.created_at DESC, r.id DESC LIMIT 1
                ) AS replacement_reason, (
                    SELECT r.relation FROM memory_replacements r WHERE r.new_memory_id=m.id
                    ORDER BY r.created_at DESC, r.id DESC LIMIT 1
                ) AS replacement_relation
                FROM memories m JOIN chain c ON c.id=m.id
                ORDER BY m.created_at ASC, m.id ASC LIMIT 100""", (memory_id,)).fetchall()
        return [self._memory(dict(row)) for row in rows]

    def correct_memory(
        self, memory_id: str, content: str, kind: str, importance: int,
        reason: str, embedding: list[float] | None = None,
    ) -> dict[str, Any] | None:
        """Replace one active version atomically, retaining the user's correction trail."""
        timestamp = now()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                previous = self.db.execute("SELECT * FROM memories WHERE id=? AND status='active'", (memory_id,)).fetchone()
                if previous is None:
                    self.db.execute("ROLLBACK")
                    return None
                content = content.strip()
                if (content, kind, importance) == (previous["content"], previous["kind"], previous["importance"]):
                    raise ValueError("纠正内容与当前记忆相同")
                item = {
                    "id": uuid.uuid4().hex, "content": content, "kind": kind,
                    "importance": importance, "source": "user_correction",
                    "created_at": timestamp, "updated_at": timestamp,
                    "embedding": json.dumps(embedding) if embedding else None,
                    "status": "active", "reinforcement": 1,
                    "supersedes_id": memory_id, "last_reinforced_at": None,
                    "source_ref": None,
                }
                self.db.execute("""INSERT INTO memories
                    (id,content,kind,importance,source,created_at,updated_at,embedding,status,reinforcement,supersedes_id,last_reinforced_at,source_ref)
                    VALUES (:id,:content,:kind,:importance,:source,:created_at,:updated_at,:embedding,:status,:reinforcement,:supersedes_id,:last_reinforced_at,:source_ref)""", item)
                self.db.execute("INSERT INTO memories_fts(id,content) VALUES (?,?)", (item["id"], content))
                if embedding:
                    self._prepare_vector_dimension(embedding, item["id"])
                    self.vector_index.upsert(item["id"], embedding)
                self.db.execute("UPDATE memories SET status='superseded',updated_at=? WHERE id=?", (timestamp, memory_id))
                self.db.execute("""INSERT INTO memory_replacements
                    (old_memory_id,new_memory_id,old_content,new_content,relation,reason,created_at)
                    VALUES (?,?,?,?,'correction',?,?)""",
                    (memory_id, item["id"], previous["content"], content, reason.strip(), timestamp))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return self.memory(item["id"])

    def update_memory(self, memory_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        current = self.memory(memory_id)
        if not current:
            return None
        previous_content = current["content"]
        current.update({k: v for k, v in data.items() if k in {"content", "kind", "importance"} and v is not None})
        current["importance"] = max(1, min(5, int(current["importance"])))
        current["updated_at"] = now()
        with self.lock:
            content_changed = current["content"] != previous_content
            self.db.execute("UPDATE memories SET content=?,kind=?,importance=?,updated_at=?,embedding=CASE WHEN ? THEN NULL ELSE embedding END WHERE id=?", (current["content"], current["kind"], current["importance"], current["updated_at"], content_changed, memory_id))
            if content_changed:
                self.db.execute("DELETE FROM memories_fts WHERE id=?", (memory_id,))
                self.db.execute("INSERT INTO memories_fts(id,content) VALUES (?,?)", (memory_id, current["content"]))
                self.vector_index.delete(memory_id)
            self.db.commit()
        if content_changed: current["embedding"] = None
        return current

    def set_memory_embedding(self, memory_id: str, embedding: list[float]) -> None:
        with self.lock:
            self._prepare_vector_dimension(embedding, memory_id)
            self.db.execute("UPDATE memories SET embedding=? WHERE id=?", (json.dumps(embedding), memory_id))
            self.vector_index.upsert(memory_id, embedding)
            self.db.commit()

    def vector_memory_candidates(self, vector: list[float], limit: int = 100) -> list[tuple[dict[str, Any], float]]:
        with self.lock:
            matches = self.vector_index.search(vector, limit)
            result = []
            for memory_id, similarity in matches:
                row = self.db.execute("SELECT * FROM memories WHERE id=? AND status='active'", (memory_id,)).fetchone()
                if row:
                    result.append((self._memory(dict(row)), similarity))
        return result

    @property
    def vector_index_status(self) -> dict[str, Any]:
        with self.lock:
            return {"enabled": self.vector_index.enabled, "backend": "sqlite-vec" if self.vector_index.enabled else "json", "dimension": self.vector_index.dimension, "error": self.vector_index.error}

    @staticmethod
    def _memory(item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        raw = result.get("embedding")
        if isinstance(raw, str):
            try: result["embedding"] = json.loads(raw)
            except json.JSONDecodeError: result["embedding"] = None
        result.setdefault("status", "active")
        result.setdefault("reinforcement", 1)
        result.setdefault("supersedes_id", None)
        result.setdefault("last_reinforced_at", None)
        return result

    def delete_memory(self, memory_id: str) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            self.db.execute("DELETE FROM memories_fts WHERE id=?", (memory_id,))
            self.vector_index.delete(memory_id)
            self.db.commit()
        return cur.rowcount > 0

    def overview(self) -> dict[str, int]:
        with self.lock:
            sessions = self.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            memories = self.db.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
            memories_superseded = self.db.execute("SELECT COUNT(*) FROM memories WHERE status='superseded'").fetchone()[0]
            traces = self.db.execute("SELECT COUNT(*) FROM turn_traces").fetchone()[0]
            memory_jobs_pending = self.db.execute("SELECT COUNT(*) FROM memory_jobs WHERE status IN ('pending','retry','running')").fetchone()[0]
            memory_jobs_failed = self.db.execute("SELECT COUNT(*) FROM memory_jobs WHERE status='failed'").fetchone()[0]
            drift_runs = self.db.execute("SELECT COUNT(*) FROM drift_runs").fetchone()[0]
        return {
            "sessions": sessions, "messages": messages, "memories": memories,
            "memories_superseded": memories_superseded, "traces": traces,
            "memory_jobs_pending": memory_jobs_pending, "memory_jobs_failed": memory_jobs_failed,
            "drift_runs": drift_runs,
        }

    def seconds_since_last_user_message(self, *, exclude_session_titles: tuple[str, ...] = ()) -> float | None:
        """Idle since last human user message. Drift session prompts are excluded by title."""
        with self.lock:
            if exclude_session_titles:
                placeholders = ",".join("?" for _ in exclude_session_titles)
                row = self.db.execute(
                    f"""SELECT m.created_at FROM messages m
                    JOIN sessions s ON s.id=m.session_id
                    WHERE m.role='user' AND s.title NOT IN ({placeholders})
                    ORDER BY m.created_at DESC LIMIT 1""",
                    exclude_session_titles,
                ).fetchone()
            else:
                row = self.db.execute(
                    "SELECT created_at FROM messages WHERE role='user' ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
        if not row:
            return None
        try:
            created = datetime.fromisoformat(str(row["created_at"]))
        except ValueError:
            return None
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds())

    def create_drift_run(
        self,
        *,
        run_id: str,
        session_id: str,
        skill: str,
        trigger: str,
        status: str = "running",
    ) -> dict[str, Any]:
        timestamp = now()
        item = {
            "id": run_id,
            "session_id": session_id,
            "skill": skill,
            "trigger": trigger,
            "status": status,
            "steps": 0,
            "summary": "",
            "trace_id": "",
            "error": "",
            "created_at": timestamp,
            "finished_at": None,
        }
        with self.lock:
            self.db.execute(
                """INSERT INTO drift_runs
                (id,session_id,skill,trigger,status,steps,summary,trace_id,error,created_at,finished_at)
                VALUES (:id,:session_id,:skill,:trigger,:status,:steps,:summary,:trace_id,:error,:created_at,:finished_at)""",
                item,
            )
            self.db.commit()
        return item

    def finish_drift_run(
        self,
        run_id: str,
        *,
        status: str,
        summary: str,
        steps: int,
        trace_id: str,
        error: str = "",
    ) -> dict[str, Any]:
        timestamp = now()
        with self.lock:
            self.db.execute(
                """UPDATE drift_runs SET status=?, summary=?, steps=?, trace_id=?, error=?, finished_at=?
                WHERE id=?""",
                (status, summary[:4000], int(steps), trace_id, error[:500], timestamp, run_id),
            )
            self.db.commit()
            row = self.db.execute("SELECT * FROM drift_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else {"id": run_id, "status": status}

    def list_drift_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM drift_runs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_drift_run(self) -> dict[str, Any] | None:
        rows = self.list_drift_runs(1)
        return rows[0] if rows else None

    def latest_completed_drift_run(self, skill: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM drift_runs WHERE skill=? AND status='completed' AND summary<>''
                ORDER BY finished_at DESC LIMIT 1""",
                (skill,),
            ).fetchone()
        return dict(row) if row else None

    def drift_runs_today(self, tz: Any) -> int:
        """Count today's runs in the given local timezone (compare in UTC)."""
        start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_local.astimezone(timezone.utc).isoformat()
        with self.lock:
            return int(
                self.db.execute(
                    "SELECT COUNT(*) FROM drift_runs WHERE created_at >= ? AND status NOT IN ('cancelled')",
                    (start_utc,),
                ).fetchone()[0]
            )

    def expire_stale_drift_runs(self, *, older_than_seconds: int = 1800) -> int:
        """Mark abandoned running drifts as failed so cooldown cannot stick forever."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(60, older_than_seconds))).isoformat()
        with self.lock:
            cursor = self.db.execute(
                """UPDATE drift_runs SET status='failed', error=COALESCE(NULLIF(error,''),'stale running expired'),
                finished_at=? WHERE status='running' AND created_at < ?""",
                (now(), cutoff),
            )
            self.db.commit()
        return int(cursor.rowcount)

    def observability_summary(self) -> dict[str, Any]:
        with self.lock:
            turns = {str(row[0]): int(row[1]) for row in self.db.execute("SELECT status,COUNT(*) FROM turn_traces GROUP BY status")}
            average = float(self.db.execute("SELECT COALESCE(AVG(duration_ms),0) FROM turn_traces").fetchone()[0])
            jobs = {str(row[0]): int(row[1]) for row in self.db.execute("SELECT status,COUNT(*) FROM memory_jobs GROUP BY status")}
            active = int(self.db.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0])
            runtime = {str(row[0]): int(row[1]) for row in self.db.execute("SELECT key,value FROM runtime_metrics")}
        return {"turns": turns, "turn_duration_ms_avg": average, "jobs": jobs, "runtime": runtime, "active_memories": active}

    def search_messages(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        with self.lock:
            rows = []
            try:
                match = f'"{query.replace(chr(34), chr(34)*2)}"'
                rows = self.db.execute("""SELECT m.*,s.title session_title FROM messages_fts f
                    JOIN messages m ON m.id=f.id JOIN sessions s ON s.id=m.session_id
                    WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts),m.created_at DESC LIMIT ?""", (match, limit)).fetchall()
            except sqlite3.OperationalError:
                pass
            if not rows:
                rows = self.db.execute("SELECT m.*, s.title session_title FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.content LIKE ? ORDER BY m.created_at DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
        return [dict(row) for row in rows]

    def keyword_memory_candidates(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        if not query.strip() or limit <= 0:
            return []
        with self.lock:
            rows: list[sqlite3.Row] = []
            try:
                match = f'"{query.replace(chr(34), chr(34)*2)}"'
                rows = self.db.execute("""SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.id
                    WHERE memories_fts MATCH ? AND m.status='active'
                    ORDER BY bm25(memories_fts),m.importance DESC LIMIT ?""", (match, limit)).fetchall()
            except sqlite3.OperationalError:
                pass
            # A full question rarely occurs verbatim in a stored fact. Search its
            # indexable fragments as well, including when the exact phrase matched
            # only a few rows. Keep exact matches first and deduplicate by ID.
            terms = self._memory_search_terms(query)
            if terms and len(rows) < limit:
                match = " OR ".join(f'"{term}"' for term in terms)
                try:
                    fragments = self.db.execute("""SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.id
                        WHERE memories_fts MATCH ? AND m.status='active'
                        ORDER BY bm25(memories_fts),m.importance DESC LIMIT ?""", (match, limit)).fetchall()
                    seen = {row["id"] for row in rows}
                    rows.extend(row for row in fragments if row["id"] not in seen)
                except sqlite3.OperationalError:
                    pass
                if not self._memory_fts_trigram:
                    # Older SQLite builds use the default FTS tokenizer, which
                    # cannot match a Chinese three-character substring.
                    where = " OR ".join("INSTR(lower(content), ?) > 0" for _ in terms)
                    fragments = self.db.execute(
                        f"SELECT * FROM memories WHERE status='active' AND ({where}) "
                        "ORDER BY importance DESC, updated_at DESC LIMIT ?",
                        (*terms, limit),
                    ).fetchall()
                    seen = {row["id"] for row in rows}
                    rows.extend(row for row in fragments if row["id"] not in seen)
            if not rows:
                rows = self.db.execute("SELECT * FROM memories WHERE status='active' AND content LIKE ? ORDER BY importance DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
        return [self._memory(dict(row)) for row in rows[:limit]]

    @staticmethod
    def _memory_search_terms(query: str) -> list[str]:
        # Question endings describe the answer being requested, not the fact to
        # find. Their trigrams can otherwise outrank a distinctive short clue.
        query = re.sub(r"(?:是)?什么[？?!！。．.\s]*$", "", query)
        terms: list[str] = []
        for word in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query.lower()):
            if len(word) < 3:
                continue  # FTS5 trigram cannot index shorter fragments.
            if "\u4e00" <= word[0] <= "\u9fff":
                fragments = (word[index:index + 3] for index in range(len(word) - 2))
            else:
                fragments = (word,)
            terms.extend(fragments)
        return list(dict.fromkeys(terms))[:16]

    def enqueue_memory_job(self, source_ref: str, user_text: str, assistant_text: str) -> dict[str, Any]:
        timestamp, job_id = now(), uuid.uuid4().hex
        with self.lock:
            self.db.execute("""INSERT OR IGNORE INTO memory_jobs
                (id,source_ref,user_text,assistant_text,status,attempts,error,available_at,lease_owner,lease_expires_at,created_at,updated_at)
                VALUES (?,?,?,?, 'pending',0,NULL,?,NULL,NULL,?,?)""", (job_id, source_ref, user_text, assistant_text, timestamp, timestamp, timestamp))
            self.db.commit()
            row = self.db.execute("SELECT * FROM memory_jobs WHERE source_ref=?", (source_ref,)).fetchone()
        return dict(row)

    def claim_memory_job(self, owner: str = "local-worker", lease_seconds: int = 180, max_retries: int = 3) -> dict[str, Any] | None:
        timestamp = now()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("""SELECT * FROM memory_jobs WHERE attempts<? AND (
                (status IN ('pending','retry') AND COALESCE(available_at,created_at)<=?) OR
                (status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                ) ORDER BY COALESCE(available_at,created_at),created_at LIMIT 1""", (max_retries, timestamp, timestamp)).fetchone()
            if row:
                self.db.execute("""UPDATE memory_jobs SET status='running',attempts=attempts+1,error=NULL,
                    lease_owner=?,lease_expires_at=?,updated_at=? WHERE id=?""", (owner, after(lease_seconds), timestamp, row["id"]))
                row = self.db.execute("SELECT * FROM memory_jobs WHERE id=?", (row["id"],)).fetchone()
            self.db.execute("COMMIT")
        return dict(row) if row else None

    def renew_memory_job(self, job_id: str, owner: str, lease_seconds: int) -> bool:
        with self.lock:
            cursor = self.db.execute("""UPDATE memory_jobs SET lease_expires_at=?,updated_at=?
                WHERE id=? AND status='running' AND lease_owner=?""", (after(lease_seconds), now(), job_id, owner))
            self.db.commit()
        return cursor.rowcount > 0

    def finish_memory_job(self, job_id: str, error: str | None = None, owner: str | None = None, max_retries: int = 3, backoff_seconds: int = 5) -> bool:
        with self.lock:
            row = self.db.execute("SELECT attempts,lease_owner FROM memory_jobs WHERE id=?", (job_id,)).fetchone()
            if not row or (owner is not None and row["lease_owner"] != owner):
                return False
            if error:
                status = "failed" if int(row["attempts"]) >= max_retries else "retry"
                delay = backoff_seconds * (2 ** max(0, int(row["attempts"]) - 1))
                self.db.execute("""UPDATE memory_jobs SET status=?,error=?,available_at=?,lease_owner=NULL,
                    lease_expires_at=NULL,updated_at=? WHERE id=?""", (status, error[:500], after(delay), now(), job_id))
            else:
                self.db.execute("""UPDATE memory_jobs SET status='completed',error=NULL,lease_owner=NULL,
                    lease_expires_at=NULL,updated_at=? WHERE id=?""", (now(), job_id))
            self.db.commit()
        return True

    def retry_memory_job(self, job_id: str) -> bool:
        with self.lock:
            cursor = self.db.execute("""UPDATE memory_jobs SET status='pending',attempts=0,error=NULL,
                available_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                WHERE id=? AND status='failed'""", (now(), now(), job_id))
            self.db.commit()
        return cursor.rowcount > 0

    def memory_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("""SELECT id,source_ref,status,attempts,error,available_at,
                lease_owner,lease_expires_at,created_at,updated_at FROM memory_jobs ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def memory_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("""SELECT id,source_ref,status,attempts,error,available_at,
                lease_owner,lease_expires_at,created_at,updated_at FROM memory_jobs WHERE id=?""", (job_id,)).fetchone()
        return dict(row) if row else None

    def stage_memory_reviews(self, job_id: str, owner: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Persist extracted candidates once per job and position, without activating them."""
        timestamp = now()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                job = self.db.execute("""SELECT source_ref FROM memory_jobs WHERE id=? AND status='running'
                    AND lease_owner=? AND lease_expires_at>?""", (job_id, owner, timestamp)).fetchone()
                if not job:
                    raise RuntimeError("memory job lease lost before staging")
                for ordinal, candidate in enumerate(candidates):
                    content = str(candidate.get("content", "")).strip()[:4000]
                    kind = str(candidate.get("kind", "fact"))
                    if not content or kind not in {"fact", "preference", "profile", "goal", "procedure"}:
                        continue
                    importance = max(1, min(5, int(candidate.get("importance", 3))))
                    self.db.execute("""INSERT OR IGNORE INTO memory_reviews
                        (id,job_id,source_ref,ordinal,content,kind,importance,status,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,'pending',?,?)""",
                        (uuid.uuid4().hex, job_id, job["source_ref"], ordinal, content, kind, importance, timestamp, timestamp))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            rows = self.db.execute("SELECT * FROM memory_reviews WHERE job_id=? ORDER BY ordinal", (job_id,)).fetchall()
        return [dict(row) for row in rows]

    def memory_reviews(self, status: str = "pending", limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            if status == "all":
                rows = self.db.execute("SELECT * FROM memory_reviews ORDER BY created_at DESC,ordinal LIMIT ?", (limit,)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM memory_reviews WHERE status=? ORDER BY created_at,ordinal LIMIT ?", (status, limit)).fetchall()
        return [dict(row) for row in rows]

    def memory_review(self, review_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM memory_reviews WHERE id=?", (review_id,)).fetchone()
        return dict(row) if row else None

    def claim_memory_review(self, review_id: str, content: str, kind: str, importance: int) -> dict[str, Any] | None:
        with self.lock:
            cursor = self.db.execute("""UPDATE memory_reviews SET status='applying',content=?,kind=?,importance=?,updated_at=?
                WHERE id=? AND status='pending'""", (content.strip(), kind, importance, now(), review_id))
            self.db.commit()
        return self.memory_review(review_id) if cursor.rowcount else None

    def finish_memory_review(self, review_id: str, memory_id: str | None, action: str) -> bool:
        with self.lock:
            cursor = self.db.execute("""UPDATE memory_reviews SET status='approved',applied_memory_id=?,applied_action=?,updated_at=?
                WHERE id=? AND status='applying'""", (memory_id, action, now(), review_id))
            self.db.commit()
        return cursor.rowcount > 0

    def reset_memory_review(self, review_id: str) -> None:
        with self.lock:
            self.db.execute("UPDATE memory_reviews SET status='pending',updated_at=? WHERE id=? AND status='applying'", (now(), review_id))
            self.db.commit()

    def reject_memory_review(self, review_id: str) -> bool:
        with self.lock:
            cursor = self.db.execute("UPDATE memory_reviews SET status='rejected',updated_at=? WHERE id=? AND status='pending'", (now(), review_id))
            self.db.commit()
        return cursor.rowcount > 0

    def undo_memory_sources(self, source_refs: list[str], dry_run: bool = False) -> dict[str, list[str]]:
        refs = [ref for ref in dict.fromkeys(source_refs) if ref]
        if not refs: return {"affected_ids": [], "restored_ids": []}
        marks = ",".join("?" for _ in refs)
        with self.lock:
            operations = self.db.execute(f"SELECT * FROM memory_operations WHERE source_ref IN ({marks}) AND undone_at IS NULL", refs).fetchall()
            state_affected = []
            for row in operations:
                current = self.db.execute("SELECT status FROM memories WHERE id=?", (row["memory_id"],)).fetchone()
                if row["action"] in {"create", "supersede"} and current and current[0] == "active":
                    state_affected.append(row["memory_id"])
            reinforced = [row["memory_id"] for row in operations if row["action"] == "reinforce"]
            affected = list(dict.fromkeys([*state_affected, *reinforced]))
            restored = list(dict.fromkeys(row["previous_id"] for row in operations if row["action"] == "supersede" and row["memory_id"] in state_affected and row["previous_id"]))
            if not dry_run:
                timestamp = now()
                if state_affected:
                    q = ",".join("?" for _ in state_affected)
                    self.db.execute(f"UPDATE memories SET status='superseded',updated_at=? WHERE id IN ({q})", (timestamp, *state_affected))
                if restored:
                    q = ",".join("?" for _ in restored)
                    self.db.execute(f"UPDATE memories SET status='active',updated_at=? WHERE id IN ({q})", (timestamp, *restored))
                for memory_id in reinforced:
                    self.db.execute("UPDATE memories SET reinforcement=MAX(1,reinforcement-1),updated_at=? WHERE id=?", (timestamp, memory_id))
                self.db.execute(f"UPDATE memory_operations SET undone_at=? WHERE source_ref IN ({marks}) AND undone_at IS NULL", (timestamp, *refs))
                self.db.commit()
        return {"affected_ids": affected, "restored_ids": restored}

    def add_trace(self, session_id: str, status: str, steps: int, duration_ms: int, memories: list[dict], tools: list[dict], error: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "session_id": session_id, "status": status, "steps": steps, "duration_ms": duration_ms, "memories_json": json.dumps(memories, ensure_ascii=False), "tools_json": json.dumps(tools, ensure_ascii=False), "metadata_json": json.dumps(metadata or {}, ensure_ascii=False), "error": error, "created_at": now()}
        with self.lock:
            self.db.execute("""INSERT INTO turn_traces
                (id,session_id,status,steps,duration_ms,memories_json,tools_json,metadata_json,error,created_at)
                VALUES (:id,:session_id,:status,:steps,:duration_ms,:memories_json,:tools_json,:metadata_json,:error,:created_at)""", item)
            calls = (metadata or {}).get("llm_calls", [])
            increments = {
                "llm_requests": len(calls),
                "llm_retries": sum(int(call.get("retries", 0) or 0) for call in calls if isinstance(call, dict)),
                "llm_duration_ms": sum(int(call.get("duration_ms", 0) or 0) for call in calls if isinstance(call, dict)),
                "llm_tokens": sum(int((call.get("usage") or {}).get("total_tokens", 0) or 0) for call in calls if isinstance(call, dict)),
            }
            for key, value in increments.items():
                self.db.execute("""INSERT INTO runtime_metrics(key,value) VALUES (?,?)
                    ON CONFLICT(key) DO UPDATE SET value=value+excluded.value""", (key, value))
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
        result["metadata"] = json.loads(result.pop("metadata_json", "{}"))
        return result

    # ---- 第十三轮: Bi-temporal + Agentic Memory 扩展 ----
    def _temporal(self, item: dict) -> dict:
        import json as _j
        item.setdefault("valid_at", item.get("created_at"))
        item.setdefault("invalid_at", None)
        for key, default in (("attributes_json", "{}"), ("entities_json", "[]"), ("provenance_json", "{}")):
            item.setdefault(key, default)
        try: item["attributes"] = _j.loads(item.get("attributes_json") or "{}")
        except Exception: item["attributes"] = {}
        try:
            entities = _j.loads(item.get("entities_json") or "[]")
            item["entities"] = entities if isinstance(entities, list) else []
        except Exception: item["entities"] = []
        try: item["provenance"] = _j.loads(item.get("provenance_json") or "{}")
        except Exception: item["provenance"] = {}
        return item

    def set_temporal(self, memory_id: str, valid_at: str | None = None, invalid_at: str | None = None) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not row: return None
            item = dict(row)
            item["valid_at"] = valid_at or item.get("valid_at") or item.get("created_at")
            item["invalid_at"] = invalid_at
            self.db.execute("UPDATE memories SET valid_at=?, invalid_at=?, updated_at=? WHERE id=?", (item["valid_at"], invalid_at, now(), memory_id))
            self.db.commit()
        return self.memory(memory_id)

    def temporal_invalidate(self, old_id: str, new_id: str, invalid_at: str | None = None) -> None:
        ts = invalid_at or now()
        with self.lock:
            self.db.execute("UPDATE memories SET invalid_at=? WHERE id=? AND invalid_at IS NULL", (ts, old_id))
            self.db.execute("UPDATE memories SET valid_at=COALESCE(valid_at,?) WHERE id=?", (ts, new_id))
            self.db.commit()

    def time_travel(self, as_of: str, limit: int = 100, query: str = "") -> list[dict]:
        with self.lock:
            params: list = [as_of, as_of]
            clause = "WHERE valid_at IS NOT NULL AND valid_at<=? AND (invalid_at IS NULL OR invalid_at>?)"
            if query:
                clause += " AND content LIKE ?"; params.append(f"%{query}%")
            rows = self.db.execute(f"SELECT * FROM memories {clause} ORDER BY importance DESC, updated_at DESC LIMIT ?", (*params, limit)).fetchall()
        out = []
        for row in rows:
            item = self._memory(dict(row)); out.append(self._temporal(item))
        return out

    def add_memory_link(self, from_id: str, to_id: str, relation: str = "related", weight: float = 1.0) -> dict | None:
        if from_id == to_id: return None
        import uuid as _u
        item = {"id": _u.uuid4().hex, "from_id": from_id, "to_id": to_id, "relation": relation[:40], "weight": weight, "created_at": now()}
        with self.lock:
            try:
                self.db.execute("INSERT OR IGNORE INTO memory_links VALUES (:id,:from_id,:to_id,:relation,:weight,:created_at)", item)
                self.db.commit()
            except Exception: return None
        return item

    def memory_neighbors(self, memory_id: str, depth: int = 1, limit: int = 20) -> list[dict]:
        seen, frontier, out = {memory_id}, [memory_id], []
        with self.lock:
            for _ in range(max(1, depth)):
                if not frontier: break
                q = ",".join("?" for _ in frontier)
                rows = self.db.execute(f"SELECT * FROM memory_links WHERE from_id IN ({q}) OR to_id IN ({q})", (*frontier, *frontier)).fetchall()
                frontier = []
                for row in rows:
                    d = dict(row)
                    for other in (d["from_id"], d["to_id"]):
                        if other not in seen:
                            seen.add(other); frontier.append(other)
                            mem = self.db.execute("SELECT * FROM memories WHERE id=? AND status='active'", (other,)).fetchone()
                            if mem:
                                m = self._memory(dict(mem)); m["link_relation"] = d["relation"]; out.append(self._temporal(m))
                            if len(out) >= limit: return out
        return out

    def record_evolution(self, memory_id: str, summary: str) -> dict:
        import uuid as _u
        item = {"id": _u.uuid4().hex, "memory_id": memory_id, "summary": summary[:2000], "created_at": now()}
        with self.lock:
            self.db.execute("INSERT INTO memory_evolutions VALUES (:id,:memory_id,:summary,:created_at)", item)
            self.db.commit()
        return item

    def memory_evolutions(self, memory_id: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM memory_evolutions WHERE memory_id=? ORDER BY created_at", (memory_id,)).fetchall()
        return [dict(r) for r in rows]
