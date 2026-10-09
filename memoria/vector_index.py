from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


class SQLiteVecIndex:
    """Optional sqlite-vec KNN index sharing the Store connection and transaction lock."""

    def __init__(self, db: sqlite3.Connection, mode: str = "auto", namespace: str = "legacy"):
        self.db = db
        self.mode = mode.lower()
        self.enabled = False
        self.error = ""
        self.namespace = namespace
        if self.mode == "json":
            self.error = "disabled by configuration"
            return
        try:
            import sqlite_vec

            self.db.enable_load_extension(True)
            try:
                sqlite_vec.load(self.db)
            finally:
                self.db.enable_load_extension(False)
            self.db.execute("""CREATE TABLE IF NOT EXISTS vector_namespaces (
                namespace TEXT PRIMARY KEY, dimension INTEGER NOT NULL)""")
            self.enabled = True
            row = self.db.execute("SELECT dimension FROM vector_namespaces WHERE namespace=?", (namespace,)).fetchone()
            if row:
                self._ensure_table(int(row[0]))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            if self.mode == "sqlite-vec":
                logger.warning("sqlite-vec 已配置但不可用，回退 JSON 扫描: %s", self.error)

    def bootstrap(self, memories: list[dict[str, Any]]) -> None:
        if not self.enabled:
            return
        for item in memories:
            raw = item.get("embedding")
            try:
                vector = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                vector = None
            if isinstance(vector, list) and vector:
                self.upsert(str(item["id"]), [float(value) for value in vector])
        if self.enabled and self.dimension is not None:
            self._ensure_table(self.dimension)
            stale = f"SELECT vm.vector_rowid FROM {self.map_table} vm LEFT JOIN memories m ON m.id=vm.memory_id WHERE m.id IS NULL OR m.status!='active'"
            self.db.execute(f"DELETE FROM {self.vec_table} WHERE rowid IN ({stale})")
            self.db.execute(f"DELETE FROM {self.map_table} WHERE vector_rowid IN ({stale})")

    def upsert(self, memory_id: str, vector: list[float]) -> None:
        if not self.enabled or not vector:
            return
        try:
            self._ensure_table(len(vector))
            row = self.db.execute(f"SELECT vector_rowid FROM {self.map_table} WHERE memory_id=?", (memory_id,)).fetchone()
            if row:
                rowid = int(row[0])
                self.db.execute(f"DELETE FROM {self.vec_table} WHERE rowid=?", (rowid,))
            else:
                cursor = self.db.execute(f"INSERT INTO {self.map_table}(memory_id) VALUES (?)", (memory_id,))
                rowid = int(cursor.lastrowid)
            self.db.execute(f"INSERT INTO {self.vec_table}(rowid,embedding) VALUES (?,?)", (rowid, json.dumps(vector)))
        except Exception as exc:
            self._disable(exc)

    def delete(self, memory_id: str) -> None:
        if not self.enabled or self.dimension is None:
            return
        self._ensure_table(self.dimension)
        row = self.db.execute(f"SELECT vector_rowid FROM {self.map_table} WHERE memory_id=?", (memory_id,)).fetchone()
        if not row:
            return
        try:
            self.db.execute(f"DELETE FROM {self.vec_table} WHERE rowid=?", (int(row[0]),))
        except sqlite3.OperationalError:
            pass
        self.db.execute(f"DELETE FROM {self.map_table} WHERE memory_id=?", (memory_id,))

    def search(self, vector: list[float], limit: int) -> list[tuple[str, float]]:
        if not self.enabled or not vector or self.dimension != len(vector):
            return []
        try:
            self._ensure_table(len(vector))
            rows = self.db.execute(f"""SELECT m.memory_id,v.distance FROM {self.vec_table} v
                JOIN {self.map_table} m ON m.vector_rowid=v.rowid
                WHERE v.embedding MATCH ? AND k=? ORDER BY v.distance""", (json.dumps(vector), max(1, limit))).fetchall()
        except Exception as exc:
            self._disable(exc)
            return []
        return [(str(row[0]), max(-1.0, min(1.0, 1.0 - float(row[1])))) for row in rows]

    @property
    def dimension(self) -> int | None:
        if not self.enabled:
            return None
        row = self.db.execute("SELECT dimension FROM vector_namespaces WHERE namespace=?", (self.namespace,)).fetchone()
        return int(row[0]) if row else None

    def _ensure_table(self, dimension: int) -> None:
        if dimension <= 0 or dimension > 65536:
            raise ValueError("invalid embedding dimension")
        current = self.dimension
        # Each model namespace and dimension owns persistent tables. Never drop old vectors.
        suffix = hashlib.sha256(f"{self.namespace}:{dimension}".encode()).hexdigest()[:24]
        self.vec_table, self.map_table = f"vec_{suffix}", f"vecmap_{suffix}"
        if current == dimension:
            return
        self.db.execute(f"CREATE TABLE IF NOT EXISTS {self.map_table} (vector_rowid INTEGER PRIMARY KEY AUTOINCREMENT,memory_id TEXT NOT NULL UNIQUE)")
        self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.vec_table} USING vec0(embedding float[{dimension}] distance_metric=cosine)")
        self.db.execute("INSERT OR REPLACE INTO vector_namespaces(namespace,dimension) VALUES (?,?)", (self.namespace, dimension))

    def _disable(self, exc: Exception) -> None:
        self.enabled = False
        self.error = f"{type(exc).__name__}: {exc}"
        logger.warning("sqlite-vec 运行失败，已回退 JSON 向量扫描: %s", self.error)
