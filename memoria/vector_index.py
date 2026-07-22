from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


class SQLiteVecIndex:
    """Optional sqlite-vec KNN index sharing the Store connection and transaction lock."""

    def __init__(self, db: sqlite3.Connection, mode: str = "auto"):
        self.db = db
        self.mode = mode.lower()
        self.enabled = False
        self.error = ""
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
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS vector_index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_vector_map (
                    vector_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL UNIQUE
                );
            """)
            self.enabled = True
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

    def upsert(self, memory_id: str, vector: list[float]) -> None:
        if not self.enabled or not vector:
            return
        try:
            self._ensure_table(len(vector))
            row = self.db.execute("SELECT vector_rowid FROM memory_vector_map WHERE memory_id=?", (memory_id,)).fetchone()
            if row:
                rowid = int(row[0])
                self.db.execute("DELETE FROM vec_memories WHERE rowid=?", (rowid,))
            else:
                cursor = self.db.execute("INSERT INTO memory_vector_map(memory_id) VALUES (?)", (memory_id,))
                rowid = int(cursor.lastrowid)
            self.db.execute("INSERT INTO vec_memories(rowid,embedding) VALUES (?,?)", (rowid, json.dumps(vector)))
        except Exception as exc:
            self._disable(exc)

    def delete(self, memory_id: str) -> None:
        if not self.enabled:
            return
        row = self.db.execute("SELECT vector_rowid FROM memory_vector_map WHERE memory_id=?", (memory_id,)).fetchone()
        if not row:
            return
        try:
            self.db.execute("DELETE FROM vec_memories WHERE rowid=?", (int(row[0]),))
        except sqlite3.OperationalError:
            pass
        self.db.execute("DELETE FROM memory_vector_map WHERE memory_id=?", (memory_id,))

    def search(self, vector: list[float], limit: int) -> list[tuple[str, float]]:
        if not self.enabled or not vector or self.dimension != len(vector):
            return []
        try:
            rows = self.db.execute("""SELECT m.memory_id,v.distance FROM vec_memories v
                JOIN memory_vector_map m ON m.vector_rowid=v.rowid
                WHERE v.embedding MATCH ? AND k=? ORDER BY v.distance""", (json.dumps(vector), max(1, limit))).fetchall()
        except Exception as exc:
            self._disable(exc)
            return []
        return [(str(row[0]), max(-1.0, min(1.0, 1.0 - float(row[1])))) for row in rows]

    @property
    def dimension(self) -> int | None:
        if not self.enabled:
            return None
        row = self.db.execute("SELECT value FROM vector_index_meta WHERE key='dimension'").fetchone()
        return int(row[0]) if row else None

    def _ensure_table(self, dimension: int) -> None:
        if dimension <= 0 or dimension > 65536:
            raise ValueError("invalid embedding dimension")
        current = self.dimension
        if current == dimension:
            return
        if current is not None:
            self.db.execute("DROP TABLE IF EXISTS vec_memories")
            self.db.execute("DELETE FROM memory_vector_map")
        self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(embedding float[{dimension}] distance_metric=cosine)")
        self.db.execute("INSERT OR REPLACE INTO vector_index_meta(key,value) VALUES ('dimension',?)", (str(dimension),))

    def _disable(self, exc: Exception) -> None:
        self.enabled = False
        self.error = f"{type(exc).__name__}: {exc}"
        logger.warning("sqlite-vec 运行失败，已回退 JSON 向量扫描: %s", self.error)
