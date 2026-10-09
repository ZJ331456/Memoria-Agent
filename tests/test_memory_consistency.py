"""Lifecycle and retrieval failures found in the October code review."""

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from memoria.memory import MemoryEngine
from memoria.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "memory.db", "json")
    yield instance
    instance.close()


def test_correct_closes_old_interval_and_opens_new(store):
    old = store.add_memory("用户喜欢红茶", "preference")
    new = store.correct_memory(old["id"], "用户喜欢绿茶", "preference", 3, "用户纠正")
    assert store.memory(old["id"])["invalid_at"] == new["valid_at"]
    assert [m["id"] for m in store.time_travel(new["valid_at"])] == [new["id"]]
    assert [m["id"] for m in store.time_travel(old["created_at"])] == [old["id"]]


def test_supersede_atomic_rollback_and_stale_target(store):
    old = store.add_memory("用户喜欢红茶", "preference")
    store.db.execute("""CREATE TEMP TRIGGER fail_replace BEFORE INSERT ON memory_replacements
        BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(Exception, match="injected failure"):
        store.add_memory("用户喜欢绿茶", "preference", supersedes_id=old["id"])
    assert store.memory(old["id"])["status"] == "active"
    assert store.memory(old["id"])["invalid_at"] is None
    assert len(store.memories(status="all")) == 1
    store.db.execute("DROP TRIGGER fail_replace")
    new = store.add_memory("用户喜欢绿茶", "preference", supersedes_id=old["id"])
    assert store.memory(old["id"])["invalid_at"] == new["valid_at"]
    with pytest.raises(ValueError):
        store.add_memory("用户喜欢白茶", "preference", supersedes_id=old["id"])
    assert len(store.memories(status="all")) == 2


def test_temporal_partial_update_preserves_end_and_normalizes_timezone(store):
    memory = store.add_memory("历史事实")
    store.set_temporal(memory["id"], "2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z")
    updated = store.set_temporal(memory["id"], valid_at="2025-01-02T08:00:00+08:00")
    assert updated["invalid_at"] == "2025-02-01T00:00:00.000000+00:00"
    assert store.time_travel("2025-01-02T00:01:00Z")[0]["id"] == memory["id"]
    assert store.time_travel("2025-02-01T00:00:00Z") == []
    with pytest.raises(ValueError):
        store.time_travel("not a date")


def test_undo_restores_separate_interval_without_erasing_history(store):
    old = store.add_memory("旧偏好", "preference")
    new = store.add_memory("新偏好", "preference", supersedes_id=old["id"], source_ref="new-source")
    store.undo_memory_sources(["new-source"])
    restored = store.memory(old["id"])
    assert restored["valid_at"] > new["created_at"]
    assert [m["id"] for m in store.time_travel(old["created_at"])] == [old["id"]]
    assert [m["id"] for m in store.time_travel(new["created_at"])] == [new["id"]]
    assert [m["id"] for m in store.time_travel(restored["valid_at"])] == [old["id"]]
    assert store.time_travel(old["created_at"])[0]["valid_at"] == old["created_at"]


def test_reinforcement_source_is_idempotent_even_after_undo(store):
    memory = store.add_memory("同一事实")
    store.reinforce_memory(memory["id"], "message-A")
    store.reinforce_memory(memory["id"], "message-A")
    assert store.memory(memory["id"])["reinforcement"] == 2
    store.undo_memory_sources(["message-A"])
    store.reinforce_memory(memory["id"], "message-A")
    assert store.memory(memory["id"])["reinforcement"] == 1
    store.reinforce_memory(memory["id"])
    assert store.memory(memory["id"])["reinforcement"] == 2


def test_history_uses_retrieval_and_never_adds_future_graph_neighbors(store):
    old = store.add_memory("项目存储使用 SQLite", "fact")
    future = store.add_memory("项目存储使用 PostgreSQL", "fact", supersedes_id=old["id"])
    store.add_memory_link(old["id"], future["id"])
    result = asyncio.run(MemoryEngine(store).retrieve("项目使用什么存储？", as_of=old["created_at"]))
    assert [m["id"] for m in result] == [old["id"]]
    assert result[0]["retrieval"]["keyword_rank"] is not None


def test_bm25_uses_term_frequency():
    single = MemoryEngine._bm25({"tea"}, Counter({"tea": 1, "other": 3}), 4, 4, {"tea": 2}, 3)
    repeated = MemoryEngine._bm25({"tea"}, Counter({"tea": 3, "other": 1}), 4, 4, {"tea": 2}, 3)
    assert repeated > single


def test_undo_batch_does_not_resurrect_withdrawn_sources(store):
    a = store.add_memory("版本 A", source_ref="A")
    b = store.add_memory("版本 B", supersedes_id=a["id"], source_ref="B")
    c = store.add_memory("版本 C", supersedes_id=b["id"], source_ref="C")
    store.undo_memory_sources(["B"])
    assert store.memory(c["id"])["status"] == "active"
    result = store.undo_memory_sources(["C"])
    assert result["restored_ids"] == [a["id"]]
    store.undo_memory_sources(["A"])
    assert store.memories() == []


def test_graph_accumulates_weighted_scores_and_resorts_existing_candidates(store):
    seeds = [store.add_memory(f"alpha seed {i}") for i in range(3)]
    target = store.add_memory("unrelated graph neighbor")
    engine = MemoryEngine(store)
    baseline = asyncio.run(engine.retrieve("alpha", limit=4, expand_graph=False))
    base = {item["id"]: item["retrieval"]["score"] for item in baseline}
    store.add_memory_link(seeds[0]["id"], target["id"], weight=0.2)
    store.add_memory_link(seeds[1]["id"], target["id"], weight=0.8)
    expanded = asyncio.run(engine.retrieve("alpha", limit=4))
    node = next(item for item in expanded if item["id"] == target["id"])
    assert node["retrieval"]["score"] == pytest.approx(0.15 * (base[seeds[0]["id"]] * 0.2 + base[seeds[1]["id"]] * 0.8), abs=1e-6)

    # Boost a candidate already present in all three lexical rankings.
    tail = baseline[-1]["id"]
    for item in baseline[:-1]:
        store.add_memory_link(item["id"], tail, weight=1)
    boosted = asyncio.run(engine.retrieve("alpha", limit=4))
    assert boosted[0]["id"] == tail


def test_evolve_replaces_version_and_numeric_fallback_respects_correction(store):
    engine = MemoryEngine(store)
    old = store.add_memory("用户目标是每周跑步 10 公里", "goal")
    updated = asyncio.run(engine.remember("用户目标是每周跑步 20 公里", "goal", 3, "manual"))
    assert updated.action == "superseded"
    corrected = store.correct_memory(updated.memory["id"], "用户目标是每周跑步 30 公里", "goal", 3, "纠正")
    automatic = asyncio.run(engine.remember("用户目标是每周跑步 40 公里", "goal", 3, "conversation"))
    assert store.memory(corrected["id"])["status"] == "active"
    assert automatic.action != "superseded"
    evolved = asyncio.run(engine.evolve(corrected["id"], "包含一次长跑", "用户补充"))
    assert evolved["supersedes_id"] == corrected["id"]
    assert store.memory(corrected["id"])["invalid_at"] == evolved["valid_at"]
    assert MemoryEngine._contradicts("用户喜欢红茶", "用户不喜欢红茶")
    assert not MemoryEngine._contradicts("用户喜欢红茶", "用户不喜欢绿茶")


@pytest.mark.parametrize("vectors", [[[1.0, 0.0]], [[1.0, 0.0], [float("nan"), 0.0]], [[1.0, 0.0], [1.0]]])
def test_backfill_rejects_entire_invalid_batch(store, vectors):
    class BrokenEmbedder:
        enabled = True
        timeout_seconds = 1

        async def embed_batch(self, texts):
            return vectors

    store.add_memory("one")
    store.add_memory("two")
    assert asyncio.run(MemoryEngine(store, BrokenEmbedder())._backfill(store.memories(), 2)) == 0
    assert all(item["embedding"] is None for item in store.memories())


@pytest.mark.parametrize("backend", ["json", "sqlite-vec"])
def test_model_and_dimension_switch_preserves_original_vectors(tmp_path, backend):
    path = tmp_path / "vectors.db"
    instance = Store(path, backend)
    try:
        if backend == "sqlite-vec" and not instance.vector_index_status["enabled"]:
            pytest.skip("sqlite-vec optional dependency not installed")
        memory = instance.add_memory("向量迁移", embedding=[1.0, 0.0])
        other = instance.add_memory("另一条旧向量", embedding=[0.0, 1.0])
        instance.configure_embedding("model-B")
        assert instance.memory(memory["id"])["embedding"] is None
        instance.set_memory_embedding(memory["id"], [0.0, 1.0])  # Same dimension, different model.
        instance.set_memory_embedding(other["id"], [0.0, 1.0, 0.0])
        assert instance.db.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 4
        instance.configure_embedding("legacy")
        assert instance.memory(memory["id"])["embedding"] == [1.0, 0.0]
        assert instance.memory(other["id"])["embedding"] == [0.0, 1.0]
        if backend == "sqlite-vec":
            assert instance.vector_memory_candidates([1.0, 0.0], 2)[0][0]["id"] == memory["id"]
    finally:
        instance.close()
    reopened = Store(path, backend)
    try:
        assert reopened.memory(memory["id"])["embedding"] == [1.0, 0.0]
    finally:
        reopened.close()


def test_cross_connection_reinforcement_and_nested_create_rollback(tmp_path):
    path = tmp_path / "concurrent.db"
    first, second = Store(path, "json"), Store(path, "json")
    try:
        memory = first.add_memory("需要幂等处理的事实")
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(instance.reinforce_memory, memory["id"], "retry-source") for instance in (first, second)]
            for future in futures:
                future.result(timeout=10)
        assert first.memory(memory["id"])["reinforcement"] == 2
        with first.lock:
            first.db.execute("BEGIN IMMEDIATE")
            first.add_memory("事务内的新版本", supersedes_id=memory["id"], embedding=[1.0, 0.0])
            assert first.db.in_transaction
            first.db.execute("ROLLBACK")
        assert first.memory(memory["id"])["status"] == "active"
        assert first.memory(memory["id"])["invalid_at"] is None
        assert first.db.execute("SELECT COUNT(*) FROM memory_validity_intervals").fetchone()[0] == 1
        assert first.db.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0
    finally:
        second.close()
        first.close()


def test_migration_reconstructs_legacy_correction_intervals(tmp_path):
    path = tmp_path / "legacy.db"
    instance = Store(path, "json")
    old = instance.add_memory("旧事实")
    new = instance.correct_memory(old["id"], "新事实", "fact", 3, "修正")
    # Model a release that only persisted statuses and replacement timestamps.
    instance.db.execute("DROP TABLE memory_validity_intervals")
    instance.db.execute("UPDATE memories SET valid_at=NULL,invalid_at=NULL")
    instance.db.commit()
    instance.close()
    reopened = Store(path, "json")
    try:
        assert reopened.memory(old["id"])["invalid_at"] == new["created_at"]
        assert [m["id"] for m in reopened.time_travel(new["created_at"])] == [new["id"]]
    finally:
        reopened.close()


def test_hot_model_switch_drops_old_backfill_and_preserves_write_identity(store):
    class SwitchingEmbedder:
        enabled = True
        timeout_seconds = 1
        namespace = "model-A"

        async def embed_batch(self, texts):
            store.configure_embedding("model-B")
            return [[1.0, 0.0] for text in texts]

        async def embed(self, text):
            store.configure_embedding("model-B")
            return [1.0, 0.0]

    original = store.add_memory("旧记忆")
    engine = MemoryEngine(store, SwitchingEmbedder())
    assert asyncio.run(engine._backfill(store.memories(), 2)) == 0
    assert store.memory(original["id"])["embedding"] is None
    store.configure_embedding("model-A")
    corrected = asyncio.run(engine.correct(original["id"], "纠正后的内容", "fact", 3, "修改"))
    assert corrected["embedding"] is None  # Old response cannot enter model-B's index.
    assert store.db.execute("SELECT namespace FROM memory_embeddings WHERE memory_id=?", (corrected["id"],)).fetchone()[0] == "model-A"
