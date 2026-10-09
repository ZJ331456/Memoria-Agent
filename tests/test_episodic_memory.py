from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoria.memory.episodic import EpisodicMemory
from memoria.memory.forgetting import ForgettingWorker
from memoria.memory.layer_settings import MemoryLayerSettings
from memoria.store import Store


@pytest.fixture
def store(tmp_path: Path):
    value = Store(tmp_path / "episodes.db")
    yield value
    value.close()


def _source(store: Store, task: str, title: str = "任务") -> tuple[str, str, str]:
    session = store.create_session(title)
    user = store.add_message(session["id"], "user", task)
    assistant = store.add_message(session["id"], "assistant", "任务已完成")
    return session["id"], user["id"], assistant["id"]


def test_layer_settings_are_flat_strict_and_publish_safe_values():
    settings = MemoryLayerSettings.from_dict({"episode_ttl_days": 30, "episodic_enabled": False})
    assert settings.episode_ttl_days == 30
    assert settings.public_dict()["semantic_chars"] == 3000
    with pytest.raises(ValueError, match="未知字段"):
        MemoryLayerSettings.from_dict({"surprise": 1})
    with pytest.raises(ValueError, match="整数"):
        MemoryLayerSettings.from_dict({"episode_ttl_days": True})
    with pytest.raises(ValueError, match="布尔值"):
        MemoryLayerSettings.from_dict({"enabled": 1})
    with pytest.raises(ValueError, match="之间"):
        MemoryLayerSettings.from_dict({"context_chars": 100})


def test_record_requires_real_same_session_roles_and_is_idempotent(store: Store):
    episodes = EpisodicMemory(store, MemoryLayerSettings())
    session_id, user_id, assistant_id = _source(store, "修复部署脚本")
    other_session, other_user, other_assistant = _source(store, "检查监控告警")
    with pytest.raises(ValueError, match="user source"):
        episodes.record(session_id, assistant_id, task="错配")
    with pytest.raises(ValueError, match="user source"):
        episodes.record(session_id, other_user, task="错配")
    with pytest.raises(ValueError, match="assistant source"):
        episodes.record(session_id, user_id, other_assistant, task="错配")
    item = episodes.record(
        session_id, user_id, assistant_id,
        task="修复部署脚本 Bearer abcdef123456789",
        outcome="completed",
        result="改正路径后成功 sk-abcdefghijklmnop",
        tool_names=["read_file", "read_file", "run_tests"],
        trace_id="trace-1",
    )
    assert item["task"].endswith("Bearer [REDACTED]")
    assert "sk-abcdefghijklmnop" not in item["result"]
    assert item["tool_names"] == ["read_file", "run_tests"]
    assert episodes.record(session_id, user_id, assistant_id, task="重试不会覆盖")["id"] == item["id"]
    assert episodes.detail(item["id"])["result"] == item["result"]
    assert episodes.events(item["id"])[0]["action"] == "record"
    with pytest.raises(ValueError, match="tool_names"):
        episodes.record(other_session, other_user, other_assistant, task="错配", tool_names=[{"arguments": "secret"}])


def test_recall_is_relevant_cross_session_and_respects_exclusion(store: Store):
    episodes = EpisodicMemory(store, MemoryLayerSettings.from_dict({"episode_top_k": 2}))
    session_a, user_a, assistant_a = _source(store, "部署脚本失败，路径拼写错误")
    first = episodes.record(session_a, user_a, assistant_a, task="部署脚本失败", result="修正脚本路径后部署成功")
    session_b, user_b, assistant_b = _source(store, "准备天气播报")
    episodes.record(session_b, user_b, assistant_b, task="准备天气播报", result="使用气象接口")
    session_c, user_c, assistant_c = _source(store, "发布脚本说明")
    second = episodes.record(session_c, user_c, assistant_c, task="发布脚本说明", result="写入发布文档")
    assert episodes.search("数据库迁移") == []
    found = episodes.search("脚本")
    assert {item["id"] for item in found} == {first["id"], second["id"]}
    assert [item["id"] for item in episodes.search("脚本", exclude_session_id=session_a)] == [second["id"]]


def test_expiry_is_immediate_and_pin_overrides_automatic_forgetting(store: Store):
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    episodes = EpisodicMemory(
        store,
        MemoryLayerSettings.from_dict({"episode_ttl_days": 1}),
        clock=lambda: current[0],
    )
    session_id, user_id, assistant_id = _source(store, "修复登录错误")
    item = episodes.record(session_id, user_id, assistant_id, task="修复登录错误", result="更新授权配置")
    assert episodes.search("登录错误")[0]["id"] == item["id"]
    current[0] += timedelta(days=2)
    assert episodes.search("登录错误") == []
    assert episodes.detail(item["id"]) is None
    assert episodes.maintenance()["expired_ids"] == [item["id"]]
    assert episodes.pin(item["id"])["pinned"] is True
    assert episodes.search("登录错误")[0]["id"] == item["id"]
    assert episodes.maintenance()["candidate_ids"] == []
    episodes.pin(item["id"], False)
    assert episodes.detail(item["id"]) is None
    assert episodes.maintenance(dry_run=False)["archived_count"] == 1
    assert episodes.stats()["archived"] == 1
    assert episodes.list(status="archive")[0]["id"] == item["id"]
    assert episodes.list(status="all")[0]["id"] == item["id"]
    assert episodes.detail(item["id"]) is None
    assert episodes.detail(item["id"], include_expired=True)["id"] == item["id"]
    assert episodes.search("登录错误") == []
    with pytest.raises(ValueError, match="dry_run"):
        episodes.maintenance(dry_run="false")


def test_capacity_dry_run_and_apply_soft_archive_only_episodes(store: Store):
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    episodes = EpisodicMemory(
        store,
        MemoryLayerSettings.from_dict({"episode_max_active": 2}),
        clock=lambda: current[0],
    )
    semantic = store.add_memory("用户偏好简短答案", "preference")
    created = []
    for index in range(4):
        session_id, user_id, assistant_id = _source(store, f"项目任务 {index}")
        created.append(episodes.record(session_id, user_id, assistant_id, task=f"项目任务 {index}", result=f"结果 {index}"))
        current[0] += timedelta(seconds=1)
    episodes.pin(created[0]["id"])
    preview = episodes.maintenance(dry_run=True)
    assert preview["capacity_ids"] == [created[1]["id"], created[2]["id"]]
    assert preview["archived_count"] == 0
    assert episodes.stats()["active"] == 4
    applied = episodes.maintenance(dry_run=False)
    assert applied["archived_count"] == 2
    assert {item["id"] for item in episodes.list()} == {created[0]["id"], created[3]["id"]}
    assert {item["id"] for item in episodes.list(status="archive")} == {created[1]["id"], created[2]["id"]}
    assert store.memory(semantic["id"])["status"] == "active"
    assert episodes.events(created[1]["id"])[0]["action"] == "archive_capacity"


def test_source_session_delete_cascades_episode_but_leaves_other_session(store: Store):
    episodes = EpisodicMemory(store, MemoryLayerSettings())
    session_a, user_a, assistant_a = _source(store, "检查项目缓存")
    session_b, user_b, assistant_b = _source(store, "检查项目索引")
    first = episodes.record(session_a, user_a, assistant_a, task="检查项目缓存")
    second = episodes.record(session_b, user_b, assistant_b, task="检查项目索引")
    assert store.delete_session(session_a)
    assert episodes.detail(first["id"]) is None
    assert episodes.search("项目缓存") == []
    assert episodes.detail(second["id"])["id"] == second["id"]
    assert any(event["action"] == "source_delete" for event in episodes.events(first["id"]))


def test_forgetting_worker_runs_maintenance_until_stopped(store: Store, monkeypatch):
    episodes = EpisodicMemory(store, MemoryLayerSettings())
    stop = asyncio.Event()
    calls = []

    def maintain(*, dry_run=True, as_of=None):
        calls.append(dry_run)
        stop.set()
        return {"count": 0}

    monkeypatch.setattr(episodes, "maintenance", maintain)
    asyncio.run(ForgettingWorker(episodes, 60).run_loop(stop))
    assert calls == [False]
