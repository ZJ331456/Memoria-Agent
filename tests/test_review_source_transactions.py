from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memoria.memory.engine import MemoryEngine
from memoria.store import MemorySourceUnavailable, Store


def test_missing_review_source_rolls_back_create_replace_and_reinforce(tmp_path: Path):
    store = Store(tmp_path / "review.db", "json")
    try:
        old = store.add_memory("用户喜欢绿茶", "preference")
        with pytest.raises(MemorySourceUnavailable):
            store.add_memory("用户喜欢红茶", "preference", supersedes_id=old["id"], source_ref="deleted", require_source=True)
        assert not store.db.in_transaction
        assert store.memory(old["id"])["status"] == "active"
        assert len(store.memories()) == 1
        with pytest.raises(MemorySourceUnavailable):
            store.reinforce_memory(old["id"], "deleted", require_source=True)
        assert not store.db.in_transaction
        assert store.memory(old["id"])["reinforcement"] == 1
        assert store.memory_history(old["id"]) == []
        assert not store.has_memory_operation("deleted", old["id"])
    finally:
        store.close()


def test_guarded_reinforcement_preserves_callers_transaction(tmp_path: Path):
    store = Store(tmp_path / "review.db", "json")
    try:
        session = store.create_session("来源")
        source = store.add_message(session["id"], "user", "喜欢绿茶")
        memory = store.add_memory("用户喜欢绿茶", "preference")
        with store.lock:
            store.db.execute("BEGIN IMMEDIATE")
            store.reinforce_memory(memory["id"], source["id"], require_source=True)
            assert store.db.in_transaction
            store.db.execute("ROLLBACK")
        assert store.memory(memory["id"])["reinforcement"] == 1
        assert not store.has_memory_operation(source["id"], memory["id"])
    finally:
        store.close()


def test_source_deleted_during_model_decision_cannot_publish(tmp_path: Path):
    store = Store(tmp_path / "review.db", "json")
    try:
        session = store.create_session("来源")
        source = store.add_message(session["id"], "user", "用户喜欢绿茶饮品")
        memory = store.add_memory("用户喜欢红茶饮品", "preference")

        async def decide(content, kind, related):
            # A separate connection models deletion while the model is awaited.
            other = Store(tmp_path / "review.db", "json")
            try:
                other.delete_session(session["id"])
            finally:
                other.close()
            await asyncio.sleep(0)
            return {"action": "supersede", "target_id": memory["id"], "reason": "用户更正"}

        engine = MemoryEngine(store, decider=decide)
        with pytest.raises(MemorySourceUnavailable):
            asyncio.run(engine.remember("用户喜欢绿茶饮品", "preference", 3, "reviewed_conversation", source["id"], require_source=True))
        assert len(store.memories()) == 1
        assert store.memory(memory["id"])["status"] == "active"
        assert not store.db.in_transaction
    finally:
        store.close()
