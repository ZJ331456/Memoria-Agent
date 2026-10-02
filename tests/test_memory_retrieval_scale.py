import asyncio
import sqlite3

import pytest

from memoria.memory import MemoryEngine
from memoria.store import Store


@pytest.mark.parametrize("legacy_fts", [False, True])
def test_paraphrased_query_finds_memory_outside_recent_candidate_window(tmp_path, legacy_fts):
    path = tmp_path / "scale.db"
    if legacy_fts:
        with sqlite3.connect(path) as db:
            db.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(id UNINDEXED, content)")
    store = Store(path, vector_backend="json")
    target = store.add_memory("我喜欢周末徒步，经常去山里", "preference", 1, "test")
    for index in range(220):
        store.add_memory(f"无关的日常记录 {index}", "fact", 5, "test")

    assert target["id"] not in {item["id"] for item in store.memories(limit=200)}
    results = asyncio.run(MemoryEngine(store).retrieve("周末徒步安排", limit=3))

    assert results[0]["id"] == target["id"]
    assert results[0]["retrieval"]["keyword_rank"] == 1


def test_keyword_candidates_keep_exact_matches_and_exclude_superseded(tmp_path):
    store = Store(tmp_path / "candidates.db", vector_backend="json")
    old = store.add_memory("周末徒步去旧路线", "preference", 3, "test")
    new = store.add_memory("周末徒步去新路线", "preference", 3, "test", supersedes_id=old["id"])

    candidates = store.keyword_memory_candidates("周末徒步去新路线")

    assert [item["id"] for item in candidates] == [new["id"]]
    assert store.keyword_memory_candidates(" ") == []


def test_question_suffix_does_not_promote_unrelated_goal(tmp_path):
    store = Store(tmp_path / "goals.db", vector_backend="json")
    store.add_memory("用户当前目标是系统学习 Rust", "goal", 4, "test")
    marathon = store.add_memory("用户计划在十月完成第一次马拉松", "goal", 4, "test")

    results = asyncio.run(MemoryEngine(store).retrieve("十月的运动目标是什么？", limit=2))

    assert results[0]["id"] == marathon["id"]


@pytest.mark.parametrize("query", ["是什么？", "什么呢？"])
def test_question_without_fact_clues_does_not_match_every_memory(tmp_path, query):
    store = Store(tmp_path / "no_clue.db", vector_backend="json")
    store.add_memory("用户计划在十月完成第一次马拉松", "goal", 4, "test")

    assert asyncio.run(MemoryEngine(store).retrieve(query, limit=2)) == []
    store.close()
