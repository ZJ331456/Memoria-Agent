"""Governed, space-scoped memory shared by logical agents.

This layer deliberately has its own tables. Existing personal memories retain
their current behavior until a caller explicitly proposes an import.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .store import Store, now

_KINDS = {"fact", "preference", "profile", "goal", "procedure"}
_ROLES = {"reader": 1, "contributor": 2, "curator": 3, "owner": 4}
_STATUSES = {"pending", "approved", "rejected", "all"}


class GovernanceError(Exception):
    """A domain error with an HTTP-compatible status for the API adapter."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class MemoryGovernance:
    """Persist proposals, approvals and isolated shared memory in a Store DB."""

    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        with store.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    visibility TEXT NOT NULL CHECK(visibility IN ('private', 'shared')),
                    owner_agent_id TEXT NOT NULL REFERENCES agents(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS space_grants (
                    space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    role TEXT NOT NULL CHECK(role IN ('reader', 'contributor', 'curator')),
                    granted_at TEXT NOT NULL,
                    PRIMARY KEY(space_id, agent_id)
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    space_id TEXT NOT NULL REFERENCES spaces(id),
                    proposer_agent_id TEXT NOT NULL REFERENCES agents(id),
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    importance INTEGER NOT NULL CHECK(importance BETWEEN 1 AND 5),
                    topic_key TEXT NOT NULL,
                    conflict_memory_id TEXT,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'approved', 'rejected')),
                    reviewer_id TEXT REFERENCES agents(id),
                    review_reason TEXT NOT NULL DEFAULT '',
                    applied_memory_id TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_proposals_space_status
                    ON proposals(space_id, status, created_at);
                CREATE TABLE IF NOT EXISTS governed_memories (
                    id TEXT PRIMARY KEY,
                    space_id TEXT NOT NULL REFERENCES spaces(id),
                    proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
                    proposer_agent_id TEXT NOT NULL REFERENCES agents(id),
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    importance INTEGER NOT NULL CHECK(importance BETWEEN 1 AND 5),
                    topic_key TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    expires_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'revoked')),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    supersedes_id TEXT REFERENCES governed_memories(id),
                    approved_by TEXT NOT NULL REFERENCES agents(id),
                    approved_at TEXT NOT NULL,
                    revoked_by TEXT,
                    revoked_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_governed_active_topic
                    ON governed_memories(space_id, topic_key) WHERE status='active';
                CREATE INDEX IF NOT EXISTS idx_governed_search
                    ON governed_memories(space_id, status, approved_at DESC);
                CREATE TABLE IF NOT EXISTS governance_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    space_id TEXT NOT NULL REFERENCES spaces(id),
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    proposal_id TEXT,
                    memory_id TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    source_type TEXT,
                    source_ref TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_governance_events_space
                    ON governance_events(space_id, seq DESC);
                CREATE TRIGGER IF NOT EXISTS governance_events_no_update
                    BEFORE UPDATE ON governance_events
                    BEGIN SELECT RAISE(ABORT, 'governance events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS governance_events_no_delete
                    BEFORE DELETE ON governance_events
                    BEGIN SELECT RAISE(ABORT, 'governance events are append-only'); END;
                """
            )
            proposal_columns = {row[1] for row in self.db.execute("PRAGMA table_info(proposals)")}
            if "conflict_memory_id" not in proposal_columns:
                self.db.execute("ALTER TABLE proposals ADD COLUMN conflict_memory_id TEXT")
            memory_columns = {row[1] for row in self.db.execute("PRAGMA table_info(governed_memories)")}
            if "proposer_agent_id" not in memory_columns:
                self.db.execute("ALTER TABLE governed_memories ADD COLUMN proposer_agent_id TEXT")
            self.db.commit()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self.store.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _agent(self, agent_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT id,name,enabled,created_at FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
        if not row or not row["enabled"]:
            raise GovernanceError(403, "Agent 不存在或已禁用")
        return dict(row)

    def _access(self, actor_id: str, space_id: str, needed: str) -> dict[str, Any]:
        self._agent(actor_id)
        row = self.db.execute("SELECT * FROM spaces WHERE id=?", (space_id,)).fetchone()
        if not row:
            raise GovernanceError(404, "记忆空间不存在")
        space = dict(row)
        if space["owner_agent_id"] == actor_id:
            role = "owner"
        elif space["visibility"] == "private":
            raise GovernanceError(403, "无权访问私有记忆空间")
        else:
            grant = self.db.execute(
                "SELECT role FROM space_grants WHERE space_id=? AND agent_id=?",
                (space_id, actor_id),
            ).fetchone()
            role = grant["role"] if grant else ""
        if _ROLES.get(role, 0) < _ROLES[needed]:
            raise GovernanceError(403, "Agent 无此记忆空间的操作权限")
        space["access_role"] = role
        return space

    def _event(
        self,
        space_id: str,
        actor_id: str,
        action: str,
        *,
        proposal_id: str | None = None,
        memory_id: str | None = None,
        reason: str = "",
        source_type: str | None = None,
        source_ref: str | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO governance_events
            (space_id,actor_id,action,proposal_id,memory_id,reason,source_type,source_ref,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (space_id, actor_id, action, proposal_id, memory_id, reason[:500], source_type, source_ref, now()),
        )

    def create_agent(self, name: str) -> dict[str, Any]:
        """Bootstrap operation: caller must restrict this at the API boundary."""
        name = name.strip()
        if not name or len(name) > 80:
            raise GovernanceError(422, "Agent 名称长度必须为 1–80")
        token = secrets.token_urlsafe(32)
        item = {
            "id": uuid.uuid4().hex,
            "name": name,
            "enabled": 1,
            "created_at": now(),
        }
        with self._transaction():
            self.db.execute(
                "INSERT INTO agents (id,name,token_hash,enabled,created_at) VALUES (?,?,?,?,?)",
                (item["id"], name, hashlib.sha256(token.encode()).hexdigest(), 1, item["created_at"]),
            )
        return {**item, "token": token}

    def authenticate(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.store.lock:
            row = self.db.execute(
                "SELECT id,name,enabled,created_at FROM agents WHERE token_hash=? AND enabled=1",
                (digest,),
            ).fetchone()
        return dict(row) if row else None

    def list_agents(self) -> list[dict[str, Any]]:
        """Administrative listing; token hashes are never selected or returned."""
        with self.store.lock:
            rows = self.db.execute(
                "SELECT id,name,enabled,created_at FROM agents ORDER BY created_at,id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _agent_event_spaces(self, agent_id: str) -> list[str]:
        rows = self.db.execute(
            """SELECT id AS space_id FROM spaces WHERE owner_agent_id=?
            UNION SELECT space_id FROM space_grants WHERE agent_id=?
            UNION SELECT space_id FROM proposals WHERE proposer_agent_id=? OR reviewer_id=?
            UNION SELECT space_id FROM governed_memories
                WHERE proposer_agent_id=? OR approved_by=? OR revoked_by=?
            UNION SELECT space_id FROM governance_events WHERE actor_id=?""",
            (agent_id,) * 8,
        ).fetchall()
        return [row["space_id"] for row in rows]

    def disable_agent(self, agent_id: str) -> bool:
        """Administrative key revocation; the API must authorize the caller."""
        with self._transaction():
            result = self.db.execute(
                "UPDATE agents SET enabled=0 WHERE id=? AND enabled=1", (agent_id,)
            )
            if result.rowcount:
                for space_id in self._agent_event_spaces(agent_id):
                    self._event(
                        space_id, "admin", "disable_agent", reason=agent_id,
                        source_type="agent", source_ref=agent_id,
                    )
            return result.rowcount > 0

    def rotate_agent_key(self, agent_id: str) -> dict[str, Any]:
        """Replace a compromised key and re-enable the agent; admin API only."""
        token = secrets.token_urlsafe(32)
        with self._transaction():
            row = self.db.execute(
                "SELECT id,name,created_at FROM agents WHERE id=?", (agent_id,)
            ).fetchone()
            if not row:
                raise GovernanceError(404, "Agent 不存在")
            self.db.execute(
                "UPDATE agents SET token_hash=?,enabled=1 WHERE id=?",
                (hashlib.sha256(token.encode()).hexdigest(), agent_id),
            )
            for space_id in self._agent_event_spaces(agent_id):
                self._event(
                    space_id, "admin", "rotate_agent_key", reason=agent_id,
                    source_type="agent", source_ref=agent_id,
                )
        return {**dict(row), "enabled": 1, "token": token}

    def create_space(self, actor_id: str, name: str, visibility: str = "shared") -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 100:
            raise GovernanceError(422, "记忆空间名称长度必须为 1–100")
        if visibility not in {"private", "shared"}:
            raise GovernanceError(422, "visibility 必须为 private 或 shared")
        item = {
            "id": uuid.uuid4().hex,
            "name": name,
            "visibility": visibility,
            "owner_agent_id": actor_id,
            "created_at": now(),
        }
        with self._transaction():
            self._agent(actor_id)
            self.db.execute(
                "INSERT INTO spaces (id,name,visibility,owner_agent_id,created_at) VALUES (:id,:name,:visibility,:owner_agent_id,:created_at)",
                item,
            )
            self._event(item["id"], actor_id, "create_space")
        return {**item, "access_role": "owner"}

    def grant(self, actor_id: str, space_id: str, agent_id: str, role: str) -> dict[str, Any]:
        if role not in {"reader", "contributor", "curator"}:
            raise GovernanceError(422, "授权角色必须为 reader、contributor 或 curator")
        with self._transaction():
            space = self._access(actor_id, space_id, "owner")
            if space["visibility"] != "shared":
                raise GovernanceError(409, "私有空间不能授权给其他 Agent")
            if agent_id == actor_id:
                raise GovernanceError(409, "空间所有者无需额外授权")
            self._agent(agent_id)
            timestamp = now()
            self.db.execute(
                """INSERT INTO space_grants (space_id,agent_id,role,granted_at) VALUES (?,?,?,?)
                ON CONFLICT(space_id,agent_id) DO UPDATE SET role=excluded.role,granted_at=excluded.granted_at""",
                (space_id, agent_id, role, timestamp),
            )
            self._event(space_id, actor_id, "grant", reason=f"{agent_id}:{role}")
        return {"space_id": space_id, "agent_id": agent_id, "role": role, "granted_at": timestamp}

    def revoke_grant(self, actor_id: str, space_id: str, agent_id: str) -> bool:
        with self._transaction():
            self._access(actor_id, space_id, "owner")
            result = self.db.execute(
                "DELETE FROM space_grants WHERE space_id=? AND agent_id=?",
                (space_id, agent_id),
            )
            if result.rowcount:
                self._event(space_id, actor_id, "revoke_grant", reason=agent_id)
            return result.rowcount > 0

    def list_grants(self, actor_id: str, space_id: str) -> list[dict[str, Any]]:
        with self.store.lock:
            space = self._access(actor_id, space_id, "curator")
            if space["visibility"] == "private":
                return []
            rows = self.db.execute(
                """SELECT g.space_id,g.agent_id,a.name AS agent_name,g.role,g.granted_at
                FROM space_grants g JOIN agents a ON a.id=g.agent_id
                WHERE g.space_id=? ORDER BY g.granted_at,g.agent_id""",
                (space_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def visible_spaces(self, actor_id: str) -> list[dict[str, Any]]:
        with self.store.lock:
            self._agent(actor_id)
            rows = self.db.execute(
                """SELECT s.*,CASE WHEN s.owner_agent_id=? THEN 'owner' ELSE g.role END AS access_role
                FROM spaces s LEFT JOIN space_grants g ON g.space_id=s.id AND g.agent_id=?
                WHERE s.owner_agent_id=? OR (s.visibility='shared' AND g.agent_id IS NOT NULL)
                ORDER BY s.created_at,s.id""",
                (actor_id, actor_id, actor_id),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _topic(value: str) -> str:
        topic = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()
        if not topic or len(topic) > 200:
            raise GovernanceError(422, "topic_key 长度必须为 1–200")
        return topic

    @staticmethod
    def _expiry(value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
            return parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, AttributeError) as exc:
            raise GovernanceError(422, "expires_at 必须是带时区的 ISO 8601 时间") from exc

    @staticmethod
    def _same_version(left: dict[str, Any] | sqlite3.Row, right: dict[str, Any] | sqlite3.Row) -> bool:
        return all(
            left[field] == right[field]
            for field in ("content", "kind", "importance", "source_type", "source_ref", "expires_at")
        )

    @staticmethod
    def _effective_status(memory: dict[str, Any] | sqlite3.Row, timestamp: str) -> str:
        if memory["status"] == "revoked":
            return "revoked"
        if memory["expires_at"] is not None and memory["expires_at"] <= timestamp:
            return "expired"
        return str(memory["status"])

    @classmethod
    def _present_memory(cls, memory: dict[str, Any] | sqlite3.Row, timestamp: str) -> dict[str, Any]:
        return {**dict(memory), "effective_status": cls._effective_status(memory, timestamp)}

    def _validate_source(self, source_type: str, source_ref: str | None, status: int = 422) -> None:
        """Validate local references inside the same transaction as publication."""
        if source_type in {"message", "conversation", "reviewed_conversation"}:
            exists = source_ref and self.db.execute("SELECT 1 FROM messages WHERE id=?", (source_ref,)).fetchone()
        elif source_type == "session":
            exists = source_ref and self.db.execute("SELECT 1 FROM sessions WHERE id=?", (source_ref,)).fetchone()
        elif source_type == "legacy_memory":
            exists = source_ref and self.db.execute("SELECT 1 FROM memories WHERE id=? AND status='active'", (source_ref,)).fetchone()
        else:
            return  # Manual input and external references require curator verification.
        if not exists:
            raise GovernanceError(status, "本地来源不存在、已删除或已失效，请重新核对来源")

    def propose(
        self,
        actor_id: str,
        space_id: str,
        content: str,
        kind: str = "fact",
        importance: int = 3,
        topic_key: str = "",
        source_type: str = "manual",
        source_ref: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content or len(content) > 4000:
            raise GovernanceError(422, "记忆内容长度必须为 1–4000")
        if kind not in _KINDS:
            raise GovernanceError(422, "无效的记忆类型")
        if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 5:
            raise GovernanceError(422, "importance 必须为 1–5")
        topic_key = self._topic(topic_key)
        source_type = source_type.strip()
        if not source_type or len(source_type) > 40:
            raise GovernanceError(422, "source_type 长度必须为 1–40")
        if source_ref is not None and len(source_ref) > 512:
            raise GovernanceError(422, "source_ref 不能超过 512 字符")
        if source_type in {"message", "conversation", "reviewed_conversation"} and not source_ref:
            raise GovernanceError(422, "对话来源必须提供 source_ref")
        item = {
            "id": uuid.uuid4().hex,
            "space_id": space_id,
            "proposer_agent_id": actor_id,
            "content": content,
            "kind": kind,
            "importance": importance,
            "topic_key": topic_key,
            "conflict_memory_id": None,
            "source_type": source_type,
            "source_ref": source_ref,
            "expires_at": self._expiry(expires_at),
            "status": "pending",
            "reviewer_id": None,
            "review_reason": "",
            "applied_memory_id": None,
            "created_at": now(),
            "decided_at": None,
        }
        with self._transaction():
            self._access(actor_id, space_id, "contributor")
            self._validate_source(source_type, source_ref)
            current = self.db.execute(
                """SELECT content,kind,importance,source_type,source_ref,expires_at,id FROM governed_memories
                WHERE space_id=? AND topic_key=? AND status='active'
                  AND (expires_at IS NULL OR expires_at>?)""",
                (space_id, topic_key, now()),
            ).fetchone()
            if current and not self._same_version(item, current):
                item["conflict_memory_id"] = current["id"]
            self.db.execute(
                """INSERT INTO proposals
                (id,space_id,proposer_agent_id,content,kind,importance,topic_key,conflict_memory_id,
                 source_type,source_ref,expires_at,status,reviewer_id,review_reason,
                 applied_memory_id,created_at,decided_at)
                VALUES (:id,:space_id,:proposer_agent_id,:content,:kind,:importance,
                        :topic_key,:conflict_memory_id,:source_type,:source_ref,:expires_at,:status,
                        :reviewer_id,:review_reason,:applied_memory_id,:created_at,:decided_at)""",
                item,
            )
            self._event(
                space_id, actor_id, "propose", proposal_id=item["id"],
                source_type=source_type, source_ref=source_ref,
            )
        return {**item, "submitted_conflict_memory_id": item["conflict_memory_id"]}

    def list_proposals(
        self, actor_id: str, space_id: str, status: str = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        if status not in _STATUSES:
            raise GovernanceError(422, "无效的提议状态")
        limit = max(1, min(int(limit), 500))
        with self.store.lock:
            space = self._access(actor_id, space_id, "contributor")
            clauses = ["space_id=?"]
            args: list[Any] = [space_id]
            if status != "all":
                clauses.append("status=?")
                args.append(status)
            if space["access_role"] == "contributor":
                clauses.append("proposer_agent_id=?")
                args.append(actor_id)
            rows = self.db.execute(
                f"SELECT * FROM proposals WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC,id DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
            current_rows = self.db.execute(
                """SELECT * FROM governed_memories
                WHERE space_id=? AND status='active'
                  AND (expires_at IS NULL OR expires_at>?)""",
                (space_id, now()),
            ).fetchall()
            by_topic = {row["topic_key"]: row for row in current_rows}
            result = []
            for row in rows:
                item = dict(row)
                item["submitted_conflict_memory_id"] = item["conflict_memory_id"]
                current = by_topic.get(item["topic_key"])
                item["conflict_memory_id"] = (
                    current["id"] if current and not self._same_version(item, current) else None
                )
                result.append(item)
        return result

    def approve(
        self,
        actor_id: str,
        proposal_id: str,
        reason: str = "",
        expected_replaces_id: str | None = None,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if len(reason) > 500:
            raise GovernanceError(422, "审核原因不能超过 500 字符")
        with self._transaction():
            row = self.db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise GovernanceError(404, "提议不存在")
            proposal = dict(row)
            self._access(actor_id, proposal["space_id"], "curator")
            if proposal["status"] != "pending":
                raise GovernanceError(409, "提议已被处理")
            try:
                self._access(proposal["proposer_agent_id"], proposal["space_id"], "contributor")
            except GovernanceError as exc:
                if exc.status_code != 403:
                    raise
                raise GovernanceError(409, "提议人已禁用或失去该空间的写入权限") from exc
            self._validate_source(proposal["source_type"], proposal["source_ref"], status=409)
            timestamp = now()
            if proposal["expires_at"] and proposal["expires_at"] <= timestamp:
                raise GovernanceError(409, "提议已过期，不能批准")

            current_row = self.db.execute(
                """SELECT * FROM governed_memories
                WHERE space_id=? AND topic_key=? AND status='active'""",
                (proposal["space_id"], proposal["topic_key"]),
            ).fetchone()
            current = dict(current_row) if current_row else None
            if current and current["expires_at"] and current["expires_at"] <= timestamp:
                self.db.execute(
                    "UPDATE governed_memories SET status='revoked',revoked_by='system',revoked_at=? WHERE id=?",
                    (timestamp, current["id"]),
                )
                self._event(
                    proposal["space_id"], "system", "expire", memory_id=current["id"],
                    reason="记忆已过期", source_type=current["source_type"],
                    source_ref=current["source_ref"],
                )
                current = None

            if current and expected_replaces_id and expected_replaces_id != current["id"]:
                raise GovernanceError(409, "当前有效版本已变化，请刷新后重试")
            if current is None and expected_replaces_id:
                raise GovernanceError(409, "当前没有可替代的有效版本")

            same = current and self._same_version(proposal, current)
            if current and not same and not expected_replaces_id:
                raise GovernanceError(409, f"同主题记忆冲突，请传 expected_replaces_id={current['id']}")

            if same:
                memory = current
                action = "confirm"
            else:
                if current:
                    self.db.execute(
                        "UPDATE governed_memories SET status='superseded' WHERE id=? AND status='active'",
                        (current["id"],),
                    )
                version_row = self.db.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM governed_memories WHERE space_id=? AND topic_key=?",
                    (proposal["space_id"], proposal["topic_key"]),
                ).fetchone()
                memory = {
                    "id": uuid.uuid4().hex,
                    "space_id": proposal["space_id"],
                    "proposal_id": proposal_id,
                    "proposer_agent_id": proposal["proposer_agent_id"],
                    "content": proposal["content"],
                    "kind": proposal["kind"],
                    "importance": proposal["importance"],
                    "topic_key": proposal["topic_key"],
                    "source_type": proposal["source_type"],
                    "source_ref": proposal["source_ref"],
                    "expires_at": proposal["expires_at"],
                    "status": "active",
                    "version": int(version_row[0]),
                    "supersedes_id": current["id"] if current else None,
                    "approved_by": actor_id,
                    "approved_at": timestamp,
                    "revoked_by": None,
                    "revoked_at": None,
                }
                self.db.execute(
                    """INSERT INTO governed_memories
                    (id,space_id,proposal_id,proposer_agent_id,content,kind,importance,topic_key,
                     source_type,source_ref,expires_at,status,version,supersedes_id,
                     approved_by,approved_at,revoked_by,revoked_at)
                    VALUES (:id,:space_id,:proposal_id,:proposer_agent_id,:content,:kind,:importance,
                            :topic_key,:source_type,:source_ref,:expires_at,:status,
                            :version,:supersedes_id,:approved_by,:approved_at,
                            :revoked_by,:revoked_at)""",
                    memory,
                )
                action = "supersede" if current else "activate"

            self.db.execute(
                """UPDATE proposals SET status='approved',reviewer_id=?,review_reason=?,
                applied_memory_id=?,decided_at=? WHERE id=? AND status='pending'""",
                (actor_id, reason, memory["id"], timestamp, proposal_id),
            )
            self._event(
                proposal["space_id"], actor_id, action, proposal_id=proposal_id,
                memory_id=memory["id"], reason=reason,
                source_type=proposal["source_type"], source_ref=proposal["source_ref"],
            )
        return {**memory, "action": action}

    def reject(self, actor_id: str, proposal_id: str, reason: str = "") -> dict[str, Any]:
        reason = reason.strip()
        if len(reason) > 500:
            raise GovernanceError(422, "审核原因不能超过 500 字符")
        with self._transaction():
            row = self.db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise GovernanceError(404, "提议不存在")
            proposal = dict(row)
            self._access(actor_id, proposal["space_id"], "curator")
            if proposal["status"] != "pending":
                raise GovernanceError(409, "提议已被处理")
            timestamp = now()
            self.db.execute(
                """UPDATE proposals SET status='rejected',reviewer_id=?,review_reason=?,
                decided_at=? WHERE id=? AND status='pending'""",
                (actor_id, reason, timestamp, proposal_id),
            )
            self._event(
                proposal["space_id"], actor_id, "reject", proposal_id=proposal_id,
                reason=reason, source_type=proposal["source_type"],
                source_ref=proposal["source_ref"],
            )
            proposal.update(status="rejected", reviewer_id=actor_id, review_reason=reason, decided_at=timestamp)
        return proposal

    def search(
        self, actor_id: str, query: str = "", space_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        from collections import Counter
        from .memory.engine import MemoryEngine

        limit = max(1, min(int(limit), 500))
        query = query.strip()
        tokens = MemoryEngine._tokens(MemoryEngine._lexical_query(query))
        with self.store.lock:
            if space_id:
                self._access(actor_id, space_id, "reader")
                spaces = [space_id]
            else:
                spaces = [space["id"] for space in self.visible_spaces(actor_id)]
            if not spaces:
                return []
            placeholders = ",".join("?" for _ in spaces)
            # ACL and validity constrain SQL candidates BEFORE ranking or Top-K.
            match = ""
            params = [*spaces, now()]
            if query:
                # A short, contiguous fragment expresses a phrase lookup. Expanding
                # "离线导出" into "导出" would return a different approved policy.
                expand_query = len(query) > 8 or bool(re.search(r"\s", query))
                terms = sorted(tokens)[:64] if expand_query else []
                match = " AND (INSTR(LOWER(content),LOWER(?))>0"
                params.append(query)
                for term in terms:
                    match += " OR INSTR(LOWER(content),?)>0"
                    params.append(term)
                match += ")"
            candidate_limit = 2000 if query else limit
            rows = self.db.execute(
                f"""SELECT * FROM governed_memories
                WHERE space_id IN ({placeholders}) AND status='active'
                  AND (expires_at IS NULL OR expires_at>?) {match}
                ORDER BY importance DESC,approved_at DESC,id DESC LIMIT ?""",
                (*params, candidate_limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        if not query or not result:
            return result[:limit]
        documents = {item["id"]: Counter(MemoryEngine._token_sequence(item["content"])) for item in result}
        frequencies = Counter(token for document in documents.values() for token in document)
        avg_len = sum(sum(doc.values()) for doc in documents.values()) / len(documents)
        scores = {mid: MemoryEngine._bm25(tokens, doc, sum(doc.values()), avg_len, frequencies, len(documents))
                  for mid, doc in documents.items()}
        result.sort(key=lambda item: (query.casefold() in item["content"].casefold(), scores[item["id"]],
                                     item["importance"], item["approved_at"]), reverse=True)
        for rank, item in enumerate(result[:limit], 1):
            item["retrieval"] = {"strategy": "acl_lexical_bm25", "rank": rank, "score": round(scores[item["id"]], 6)}
        return result[:limit]

    def detail(self, actor_id: str, memory_id: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.db.execute("SELECT * FROM governed_memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise GovernanceError(404, "治理记忆不存在")
            space = self._access(actor_id, row["space_id"], "reader")
            timestamp = now()
            if _ROLES[space["access_role"]] < _ROLES["curator"] and (
                self._effective_status(row, timestamp) in {"revoked", "expired"}
            ):
                raise GovernanceError(404, "治理记忆不存在")
            return self._present_memory(row, timestamp)

    def lineage(self, actor_id: str, memory_id: str) -> list[dict[str, Any]]:
        with self.store.lock:
            row = self.db.execute(
                "SELECT space_id,topic_key,status,expires_at FROM governed_memories WHERE id=?", (memory_id,)
            ).fetchone()
            if not row:
                raise GovernanceError(404, "治理记忆不存在")
            space = self._access(actor_id, row["space_id"], "reader")
            can_audit = _ROLES[space["access_role"]] >= _ROLES["curator"]
            timestamp = now()
            if not can_audit:
                if self._effective_status(row, timestamp) in {"revoked", "expired"}:
                    raise GovernanceError(404, "治理记忆不存在")
            rows = self.db.execute(
                """SELECT * FROM governed_memories WHERE space_id=? AND topic_key=?
                ORDER BY version ASC,id ASC""",
                (row["space_id"], row["topic_key"]),
            ).fetchall()
            if not can_audit:
                rows = [
                    item for item in rows
                    if self._effective_status(item, timestamp) not in {"revoked", "expired"}
                ]
        return [self._present_memory(item, timestamp) for item in rows]

    def revoke_memory(self, actor_id: str, memory_id: str, reason: str = "") -> dict[str, Any]:
        reason = reason.strip()
        if len(reason) > 500:
            raise GovernanceError(422, "撤回原因不能超过 500 字符")
        with self._transaction():
            row = self.db.execute("SELECT * FROM governed_memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise GovernanceError(404, "治理记忆不存在")
            item = dict(row)
            self._access(actor_id, item["space_id"], "curator")
            if item["status"] != "active":
                raise GovernanceError(409, "只能撤回有效版本")
            timestamp = now()
            topic_versions = self.db.execute(
                """SELECT * FROM governed_memories
                WHERE space_id=? AND topic_key=? AND status IN ('active','superseded')
                ORDER BY version DESC""",
                (item["space_id"], item["topic_key"]),
            ).fetchall()
            self.db.execute(
                """UPDATE governed_memories SET status='revoked',revoked_by=?,revoked_at=?
                WHERE space_id=? AND topic_key=? AND status IN ('active','superseded')""",
                (actor_id, timestamp, item["space_id"], item["topic_key"]),
            )
            for version in topic_versions:
                self._event(
                    item["space_id"], actor_id,
                    "revoke" if version["id"] == memory_id else "revoke_history",
                    proposal_id=version["proposal_id"], memory_id=version["id"],
                    reason=reason, source_type=version["source_type"],
                    source_ref=version["source_ref"],
                )
            item.update(status="revoked", revoked_by=actor_id, revoked_at=timestamp)
        return item

    def events(self, actor_id: str, space_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.store.lock:
            self._access(actor_id, space_id, "curator")
            rows = self.db.execute(
                "SELECT * FROM governance_events WHERE space_id=? ORDER BY seq DESC LIMIT ?",
                (space_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]
