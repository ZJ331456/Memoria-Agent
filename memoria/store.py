from __future__ import annotations

import json
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
            """)
            self.db.commit()
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
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, updated_at DESC)")
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
        self.db.execute("INSERT INTO memories_fts(id,content) SELECT id,content FROM memories WHERE id NOT IN (SELECT id FROM memories_fts)")
        self.db.execute("INSERT INTO messages_fts(id,content) SELECT id,content FROM messages WHERE id NOT IN (SELECT id FROM messages_fts)")

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
            self.db.execute("INSERT INTO messages_fts(id,content) VALUES (?,?)", (item["id"], item["content"]))
            self.db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (item["created_at"], session_id))
            self.db.commit()
        return item

    def rename_session(self, session_id: str, title: str) -> None:
        with self.lock:
            self.db.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title[:80], now(), session_id))
            self.db.commit()

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
        return {"sessions": sessions, "messages": messages, "memories": memories, "memories_superseded": memories_superseded, "traces": traces, "memory_jobs_pending": memory_jobs_pending, "memory_jobs_failed": memory_jobs_failed}

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
        with self.lock:
            rows = []
            try:
                match = f'"{query.replace(chr(34), chr(34)*2)}"'
                rows = self.db.execute("""SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.id
                    WHERE memories_fts MATCH ? AND m.status='active'
                    ORDER BY bm25(memories_fts),m.importance DESC LIMIT ?""", (match, limit)).fetchall()
            except sqlite3.OperationalError:
                pass
            if not rows:
                rows = self.db.execute("SELECT * FROM memories WHERE status='active' AND content LIKE ? ORDER BY importance DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
        return [self._memory(dict(row)) for row in rows]

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
