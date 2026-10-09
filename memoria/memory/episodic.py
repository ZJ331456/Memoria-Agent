"""Personal, source-linked task outcomes; never a store for model reasoning."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..observability.tracing import _redact_text
from ..store import Store
from .layer_settings import MemoryLayerSettings


_OUTCOMES = {"completed", "failed", "cancelled"}
_STOPWORDS = {"什么", "怎么", "如何", "是否", "任务", "结果", "之前", "我的", "the", "what", "how", "task"}
_WORDS = re.compile(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]+")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _text(value: str, limit: int) -> str:
    return _redact_text(str(value).strip())[:limit]


class EpisodicMemory:
    """Stores bounded outcomes tied to real personal conversation messages."""

    def __init__(
        self,
        store: Store,
        settings: MemoryLayerSettings,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with store.lock:
            store.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_episodes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    user_message_id TEXT NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                    assistant_message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
                    task TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('completed','failed','cancelled')),
                    result TEXT NOT NULL DEFAULT '',
                    tool_names_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    trace_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    archived_at TEXT,
                    archive_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_memory_episodes_visible
                    ON memory_episodes(status,expires_at,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_episodes_session
                    ON memory_episodes(session_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS memory_episode_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_episode_events_episode
                    ON memory_episode_events(episode_id,seq DESC);
                CREATE TRIGGER IF NOT EXISTS memory_episode_events_no_update
                    BEFORE UPDATE ON memory_episode_events
                    BEGIN SELECT RAISE(ABORT, 'episode events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS memory_episode_events_no_delete
                    BEFORE DELETE ON memory_episode_events
                    BEGIN SELECT RAISE(ABORT, 'episode events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS memory_episode_source_delete
                    AFTER DELETE ON memory_episodes
                    BEGIN
                        INSERT INTO memory_episode_events(episode_id,action,reason,created_at)
                        VALUES(OLD.id,'source_delete','episode source or session deleted',
                               strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
                    END;
                """
            )
            store.db.commit()

    def _now(self) -> str:
        return _utc(self.clock()).isoformat()

    @staticmethod
    def _present(row: Any) -> dict[str, Any]:
        item = dict(row)
        try:
            names = json.loads(item.pop("tool_names_json"))
        except (TypeError, ValueError):
            names = []
        item["tool_names"] = names if isinstance(names, list) else []
        item["pinned"] = bool(item["pinned"])
        return item

    def _event(self, episode_id: str, action: str, reason: str = "") -> None:
        self.store.db.execute(
            "INSERT INTO memory_episode_events(episode_id,action,reason,created_at) VALUES(?,?,?,?)",
            (episode_id, action, _text(reason, 300), self._now()),
        )

    def _validate_source(self, session_id: str, user_message_id: str, assistant_message_id: str | None) -> None:
        user = self.store.db.execute(
            "SELECT session_id,role FROM messages WHERE id=?", (user_message_id,)
        ).fetchone()
        if not user or user["session_id"] != session_id or user["role"] != "user":
            raise ValueError("episode user source must be a user message in this session")
        if assistant_message_id is not None:
            assistant = self.store.db.execute(
                "SELECT session_id,role FROM messages WHERE id=?", (assistant_message_id,)
            ).fetchone()
            if not assistant or assistant["session_id"] != session_id or assistant["role"] != "assistant":
                raise ValueError("episode assistant source must be an assistant message in this session")

    def record(
        self,
        session_id: str,
        user_message_id: str,
        assistant_message_id: str | None = None,
        task: str = "",
        outcome: str = "completed",
        result: str = "",
        tool_names: list[str] | None = None,
        error: str = "",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.enabled or not self.settings.episodic_enabled:
            raise RuntimeError("episodic memory is disabled")
        if outcome not in _OUTCOMES:
            raise ValueError("episode outcome must be completed, failed or cancelled")
        task_text = _text(task, 600)
        if not task_text:
            raise ValueError("episode task cannot be empty")
        if tool_names is not None and (not isinstance(tool_names, list) or any(not isinstance(name, str) for name in tool_names)):
            raise ValueError("tool_names must contain only tool names")
        names = list(dict.fromkeys(_text(name, 80) for name in (tool_names or []) if name.strip()))[:20]
        timestamp = self._now()
        item = {
            "id": uuid.uuid4().hex,
            "session_id": session_id,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "task": task_text,
            "outcome": outcome,
            "result": _text(result, 1_200),
            "tool_names_json": json.dumps(names, ensure_ascii=False),
            "error": _text(error, 300),
            "trace_id": _text(trace_id, 100) if trace_id else None,
            "status": "active",
            "pinned": 0,
            "created_at": timestamp,
            "expires_at": (datetime.fromisoformat(timestamp) + timedelta(days=self.settings.episode_ttl_days)).isoformat(),
            "archived_at": None,
            "archive_reason": "",
        }
        with self.store.lock:
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                self._validate_source(session_id, user_message_id, assistant_message_id)
                existing = self.store.db.execute(
                    "SELECT * FROM memory_episodes WHERE user_message_id=?", (user_message_id,)
                ).fetchone()
                if existing:
                    self.store.db.execute("COMMIT")
                    return self._present(existing)
                self.store.db.execute(
                    """INSERT INTO memory_episodes
                    (id,session_id,user_message_id,assistant_message_id,task,outcome,result,
                     tool_names_json,error,trace_id,status,pinned,created_at,expires_at,
                     archived_at,archive_reason)
                    VALUES (:id,:session_id,:user_message_id,:assistant_message_id,:task,:outcome,:result,
                            :tool_names_json,:error,:trace_id,:status,:pinned,:created_at,:expires_at,
                            :archived_at,:archive_reason)""",
                    item,
                )
                self._event(item["id"], "record", outcome)
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
        return self._present(item)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        terms: set[str] = set()
        for match in _WORDS.findall(text.lower()):
            if "\u4e00" <= match[0] <= "\u9fff":
                terms.update(match[i:i + 2] for i in range(len(match) - 1))
                if len(match) == 2:
                    terms.add(match)
            else:
                terms.add(match)
        return terms - _STOPWORDS

    def search(
        self,
        query: str,
        limit: int | None = None,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.settings.enabled or not self.settings.episodic_enabled:
            return []
        query = query.strip()[:300]
        tokens = self._tokens(query)
        ceiling = self.settings.episode_top_k
        if not query or not tokens or ceiling <= 0:
            return []
        count = ceiling if limit is None else min(ceiling, max(0, int(limit)))
        if count <= 0:
            return []
        timestamp = self._now()
        with self.store.lock:
            rows = self.store.db.execute(
                """SELECT e.* FROM memory_episodes e
                JOIN messages u ON u.id=e.user_message_id AND u.session_id=e.session_id AND u.role='user'
                LEFT JOIN messages a ON a.id=e.assistant_message_id
                WHERE e.status='active' AND (e.expires_at>? OR e.pinned=1)
                  AND (e.assistant_message_id IS NULL OR (a.session_id=e.session_id AND a.role='assistant'))
                  AND (? IS NULL OR e.session_id<>?)
                ORDER BY e.created_at DESC,e.id DESC""",
                (timestamp, exclude_session_id, exclude_session_id),
            ).fetchall()
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        lowered = query.casefold()
        for row in rows:
            item = self._present(row)
            task_tokens = self._tokens(item["task"])
            result_tokens = self._tokens(item["result"])
            overlap = tokens & (task_tokens | result_tokens)
            # A single generic bigram in a multiword question is not enough
            # evidence to surface an unrelated episode.
            if len(overlap) < (2 if len(tokens) >= 3 else 1):
                continue
            score = 3 * len(tokens & task_tokens) + len(tokens & result_tokens)
            if lowered in item["task"].casefold():
                score += 8
            elif lowered in item["result"].casefold():
                score += 4
            ranked.append((score, item["created_at"], item))
        ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [item for _, _, item in ranked[:count]]

    def list(self, limit: int = 100, status: str = "active") -> list[dict[str, Any]]:
        if status not in {"active", "archive", "all"}:
            raise ValueError("status must be active, archive or all")
        limit = max(1, min(int(limit), 500))
        where: list[str] = []
        args: list[Any] = []
        if status == "active":
            where.append("(e.expires_at>? OR e.pinned=1)")
            args.append(self._now())
        if status != "all":
            where.append("e.status=?")
            args.append("archived" if status == "archive" else "active")
        where_sql = " AND ".join(where) if where else "1=1"
        with self.store.lock:
            rows = self.store.db.execute(
                """SELECT e.* FROM memory_episodes e
                JOIN messages u ON u.id=e.user_message_id AND u.session_id=e.session_id AND u.role='user'
                LEFT JOIN messages a ON a.id=e.assistant_message_id
                WHERE """ + where_sql + """
                  AND (e.assistant_message_id IS NULL OR (a.session_id=e.session_id AND a.role='assistant'))
                ORDER BY e.created_at DESC,e.id DESC LIMIT ?""",
                (*args, limit),
            ).fetchall()
        return [self._present(row) for row in rows]

    def detail(self, episode_id: str, include_expired: bool = False) -> dict[str, Any] | None:
        if type(include_expired) is not bool:
            raise ValueError("include_expired must be boolean")
        with self.store.lock:
            row = self.store.db.execute(
                """SELECT e.* FROM memory_episodes e
                JOIN messages u ON u.id=e.user_message_id AND u.session_id=e.session_id AND u.role='user'
                LEFT JOIN messages a ON a.id=e.assistant_message_id
                WHERE e.id=? AND (? OR e.expires_at>? OR e.pinned=1)
                  AND (e.assistant_message_id IS NULL OR (a.session_id=e.session_id AND a.role='assistant'))""",
                (episode_id, int(include_expired), self._now()),
            ).fetchone()
        return self._present(row) if row else None

    def pin(self, episode_id: str, pinned: bool = True) -> dict[str, Any] | None:
        if type(pinned) is not bool:
            raise ValueError("pinned must be boolean")
        with self.store.lock:
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                cursor = self.store.db.execute(
                    "UPDATE memory_episodes SET pinned=? WHERE id=? AND status='active'",
                    (int(pinned), episode_id),
                )
                if not cursor.rowcount:
                    self.store.db.execute("COMMIT")
                    return None
                self._event(episode_id, "pin" if pinned else "unpin")
                row = self.store.db.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
        return self._present(row)

    def archive(self, episode_id: str, reason: str = "") -> dict[str, Any] | None:
        with self.store.lock:
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.store.db.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
                if not row:
                    self.store.db.execute("COMMIT")
                    return None
                if row["status"] == "active":
                    self.store.db.execute(
                        "UPDATE memory_episodes SET status='archived',archived_at=?,archive_reason=? WHERE id=?",
                        (self._now(), _text(reason, 300), episode_id),
                    )
                    self._event(episode_id, "archive", reason)
                    row = self.store.db.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
        return self._present(row)

    @staticmethod
    def _as_of(value: datetime | str | None, default: str) -> str:
        if value is None:
            return default
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(parsed, datetime):
            raise ValueError("as_of must be an ISO datetime")
        return _utc(parsed).isoformat()

    def maintenance(self, dry_run: bool = True, as_of: datetime | str | None = None) -> dict[str, Any]:
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be boolean")
        timestamp = self._as_of(as_of, self._now())
        with self.store.lock:
            if not dry_run:
                self.store.db.execute("BEGIN IMMEDIATE")
            try:
                rows = self.store.db.execute(
                    "SELECT id,pinned,created_at,expires_at FROM memory_episodes WHERE status='active' ORDER BY created_at,id"
                ).fetchall()
                expired = [row["id"] for row in rows if not row["pinned"] and row["expires_at"] <= timestamp]
                expired_set = set(expired)
                remaining = [row for row in rows if row["id"] not in expired_set]
                overflow = max(0, len(remaining) - self.settings.episode_max_active)
                capacity = [row["id"] for row in remaining if not row["pinned"]][:overflow]
                candidates = [*expired, *capacity]
                if not dry_run:
                    for episode_id in expired:
                        self.store.db.execute(
                            "UPDATE memory_episodes SET status='archived',archived_at=?,archive_reason='expired' WHERE id=? AND status='active'",
                            (timestamp, episode_id),
                        )
                        self._event(episode_id, "archive_expired", "TTL elapsed")
                    for episode_id in capacity:
                        self.store.db.execute(
                            "UPDATE memory_episodes SET status='archived',archived_at=?,archive_reason='capacity' WHERE id=? AND status='active'",
                            (timestamp, episode_id),
                        )
                        self._event(episode_id, "archive_capacity", "active episode limit exceeded")
                    self.store.db.execute("COMMIT")
            except BaseException:
                if not dry_run:
                    self.store.db.execute("ROLLBACK")
                raise
        return {
            "dry_run": dry_run,
            "as_of": timestamp,
            "expired_ids": expired,
            "capacity_ids": capacity,
            "candidate_ids": candidates,
            "count": len(candidates),
            "archived_count": 0 if dry_run else len(candidates),
            "pinned_protected": sum(bool(row["pinned"]) for row in rows),
            "active_before": len(rows),
            "active_after": len(rows) - len(candidates),
        }

    def events(self, episode_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.store.lock:
            if episode_id:
                rows = self.store.db.execute(
                    "SELECT * FROM memory_episode_events WHERE episode_id=? ORDER BY seq DESC LIMIT ?",
                    (episode_id, limit),
                ).fetchall()
            else:
                rows = self.store.db.execute(
                    "SELECT * FROM memory_episode_events ORDER BY seq DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        timestamp = self._now()
        with self.store.lock:
            row = self.store.db.execute(
                """SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN status='archived' THEN 1 ELSE 0 END) AS archived,
                   SUM(CASE WHEN status='active' AND (expires_at>? OR pinned=1) THEN 1 ELSE 0 END) AS visible,
                   SUM(CASE WHEN status='active' AND expires_at<=? AND pinned=0 THEN 1 ELSE 0 END) AS expired,
                   SUM(CASE WHEN status='active' AND pinned=1 THEN 1 ELSE 0 END) AS pinned
                   FROM memory_episodes""",
                (timestamp, timestamp),
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("total", "active", "archived", "visible", "expired", "pinned")}
