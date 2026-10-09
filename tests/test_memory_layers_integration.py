"""Runtime and API contracts for source-linked task episodes."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.llm import ChatResult, ContextLengthError
from memoria.memory.budget import TurnMemoryBudget
from memoria.memory.planner import MemoryQueryPlanner


def _app(
    tmp_path: Path, *, layers_enabled: bool = True, skills_root: Path | None = None,
    episodic_chars: int | None = None, procedural_chars: int | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.toml"
    layer_overrides = "".join(
        f"{name}={value}\n" for name, value in (
            ("episodic_chars", episodic_chars), ("procedural_chars", procedural_chars)
        ) if value is not None
    )
    config.write_text(
        f'[llm.main]\nmodel="test"\napi_key="x"\nbase_url="http://example.test/v1"\n'
        f'[server.security]\napi_token="admin-secret"\n'
        f'[agent.mcp]\nenabled=false\n'
        + (f'[agent.skills]\ndirectory="{skills_root}"\n' if skills_root else '')
        + f'[memory.layers]\nenabled={str(layers_enabled).lower()}\n{layer_overrides}'
        + f'[storage]\ndatabase="{tmp_path / "layers.db"}"\n',
        encoding="utf-8",
    )
    return create_app(config)


def _without_model_retrieval(app):
    runtime = app.state.service.runtime
    runtime.retrieval_planner = MemoryQueryPlanner()

    async def no_semantic_memories(*_args, **_kwargs):
        return []

    runtime.memory.retrieve = no_semantic_memories
    return runtime


def test_episode_runtime_recall_source_and_governance_api(tmp_path: Path):
    app = _app(tmp_path)
    runtime = _without_model_retrieval(app)
    seen_messages = []

    async def fake_chat(messages, **_kwargs):
        seen_messages.append(messages)
        return ChatResult(content="已整理客户需求文档", raw_message={"content": "已整理客户需求文档"})

    runtime.llm.chat = fake_chat
    first = app.state.store.create_session("第一个任务")
    assistant, _, trace = asyncio.run(runtime.run(
        first["id"], "整理客户需求文档", skip_memory_enqueue=True,
    ))
    assert assistant["content"] == "已整理客户需求文档"
    episodes = app.state.service.episodes
    assert episodes is not None
    recorded = episodes.list()
    assert len(recorded) == 1 and recorded[0]["outcome"] == "completed"
    assert recorded[0]["trace_id"] == trace["id"]
    assert recorded[0]["assistant_message_id"] == assistant["id"]
    assert recorded[0]["user_message_id"] != assistant["id"]
    assert trace["metadata"]["memory_layers"]["section_stats"]
    assert trace["metadata"]["memory_tool_budget"]["calls"] == 0
    assert "truncated_messages" in trace["metadata"]["context_budget"]
    assert "preserved_context_frame" in trace["metadata"]["context_budget"]
    recall_tool = runtime.tools.get("recall_episodes")
    assert recall_tool is not None and recall_tool.risk == "read-only"
    tool_result = asyncio.run(runtime.tools.execute("recall_episodes", {"query": "整理客户需求文档"}))
    assert tool_result.ok and recorded[0]["user_message_id"] in tool_result.content

    second = app.state.store.create_session("第二个任务")
    asyncio.run(runtime.run(second["id"], "整理客户需求文档", skip_memory_enqueue=True))
    injected_frame = seen_messages[-1][1]["content"]
    assert "Past Task Episodes" in injected_frame
    assert "不是已验证的用户事实" in injected_frame
    assert recorded[0]["id"] in injected_frame
    assert recorded[0]["user_message_id"] in injected_frame

    client = TestClient(app)
    admin = {"Authorization": "Bearer admin-secret"}
    item_path = f"/api/episodes/{recorded[0]['id']}"
    assert client.get("/api/episodes", headers={"X-Agent-Key": "not-a-global-token"}).status_code == 401
    assert client.get("/api/memory-layers", headers={"X-Agent-Key": "not-a-global-token"}).status_code == 401
    assert client.get("/api/memory-layers", headers=admin).json()["episodic"]["total"] == 2
    assert client.get(item_path, headers=admin).json()["user_message_id"] == recorded[0]["user_message_id"]
    source = client.get(f"/api/messages/{recorded[0]['user_message_id']}/source", headers=admin)
    assert source.status_code == 200 and source.json()["session_id"] == first["id"]
    assert client.get("/api/episodes", params={"query": "整理客户需求文档"}, headers=admin).status_code == 200
    assert client.patch(item_path, json={"pin": "true"}, headers=admin).status_code == 422
    assert client.patch(item_path, json={"pin": True, "archive": True}, headers=admin).status_code == 422
    assert client.patch(item_path, json={"pin": True}, headers=admin).json()["pinned"] is True
    assert client.post("/api/episodes/maintenance", headers=admin).json()["dry_run"] is True
    assert client.patch(item_path, json={"archive": True, "reason": "用户要求归档"}, headers=admin).json()["status"] == "archived"
    assert client.get(item_path, headers=admin).status_code == 200
    assert client.get("/api/episodes", params={"status": "archive"}, headers=admin).json()[0]["id"] == recorded[0]["id"]
    assert client.delete(f"/api/sessions/{first['id']}", headers=admin).status_code == 204
    assert client.get(item_path, headers=admin).status_code == 404
    assert client.get(f"/api/messages/{recorded[0]['user_message_id']}/source", headers=admin).status_code == 404
    app.state.store.close()


def test_failed_and_cancelled_chat_record_status_without_masking_error(tmp_path: Path):
    app = _app(tmp_path)
    runtime = _without_model_retrieval(app)

    async def fail(_messages, **_kwargs):
        raise RuntimeError("model unavailable")

    runtime.llm.chat = fail
    session = app.state.store.create_session("failed")
    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(runtime.run(session["id"], "整理失败任务", skip_memory_enqueue=True))

    async def cancel(_messages, **_kwargs):
        raise asyncio.CancelledError()

    runtime.llm.chat = cancel
    cancelled = app.state.store.create_session("cancelled")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.run(cancelled["id"], "整理取消任务", skip_memory_enqueue=True))
    recorded = app.state.service.episodes.list(status="all")
    assert {item["outcome"] for item in recorded} == {"failed", "cancelled"}
    assert all(item["assistant_message_id"] is None for item in recorded)
    assert all(item["trace_id"] for item in recorded)
    app.state.store.close()


def test_same_session_episode_recall_and_actual_injection_count(tmp_path: Path):
    app = _app(tmp_path)
    runtime = _without_model_retrieval(app)
    seen_messages = []

    async def fake_chat(messages, **_kwargs):
        seen_messages.append(messages)
        return ChatResult(content="整理完成", raw_message={"content": "整理完成"})

    runtime.llm.chat = fake_chat
    session = app.state.store.create_session("连续任务")
    asyncio.run(runtime.run(session["id"], "整理客户需求文档", skip_memory_enqueue=True))
    first_id = app.state.service.episodes.list()[0]["id"]
    _, _, trace = asyncio.run(runtime.run(session["id"], "整理客户需求文档", skip_memory_enqueue=True))
    assert first_id in seen_messages[-1][1]["content"]
    assert trace["metadata"]["episode_retrieval"] == {
        "matched": 1, "injected": 1, "ids": [first_id],
    }
    app.state.store.close()

    capped = _app(tmp_path / "capped", episodic_chars=0)
    capped_runtime = _without_model_retrieval(capped)
    capped_runtime.llm.chat = fake_chat
    capped_session = capped.state.store.create_session("不注入")
    user = capped.state.store.add_message(capped_session["id"], "user", "整理客户需求文档")
    capped.state.service.episodes.record(capped_session["id"], user["id"], task="整理客户需求文档", result="已完成")
    _, _, capped_trace = asyncio.run(capped_runtime.run(capped_session["id"], "整理客户需求文档", skip_memory_enqueue=True))
    assert capped_trace["metadata"]["episode_retrieval"]["matched"] == 1
    assert capped_trace["metadata"]["episode_retrieval"]["injected"] == 0
    capped.state.store.close()


def test_parallel_memory_tool_reads_share_one_turn_budget(tmp_path: Path):
    app = _app(tmp_path)
    runtime = app.state.service.runtime
    budget = TurnMemoryBudget(max_calls=1, max_chars=300)
    budget.account_initial(30)
    calls = [
        {"id": "one", "name": "tool_call", "arguments": {"name": "search_history", "arguments": {"query": "任务", "limit": 1}}},
        {"id": "two", "name": "tool_call", "arguments": {"name": "search_history", "arguments": {"query": "任务", "limit": 1}}},
    ]
    records = asyncio.run(runtime._execute_tools_parallel(calls, set(), 1, None, budget))
    assert sum(item["ok"] for item in records) == 1
    assert sum("未执行" in item["content"] for item in records) == 1
    assert budget.public_dict()["calls"] == 1
    assert budget.public_dict()["blocked_calls"] == 1
    exhausted = TurnMemoryBudget(max_calls=1, max_chars=1)
    exhausted_record = asyncio.run(runtime._execute_tools_parallel(calls[:1], set(), 1, None, exhausted))[0]
    assert exhausted_record["budget_blocked"] is True and exhausted_record["ok"] is False
    session = app.state.store.create_session("history")
    app.state.store.add_message(session["id"], "user", "独特任务内容" * 30)
    truncated = TurnMemoryBudget(max_calls=1, max_chars=150)
    long_call = {"id": "long", "name": "tool_call", "arguments": {
        "name": "search_history", "arguments": {"query": "独特任务内容", "limit": 1},
    }}
    truncated_record = asyncio.run(runtime._execute_tools_parallel([long_call], set(), 1, None, truncated))[0]
    assert truncated_record["budget_truncated"] is True and truncated_record["ok"] is True
    app.state.store.close()


def test_disabled_layers_report_status_without_exposing_episode_endpoints(tmp_path: Path):
    app = _app(tmp_path, layers_enabled=False)
    client = TestClient(app)
    admin = {"Authorization": "Bearer admin-secret"}
    assert client.get("/api/memory-layers", headers=admin).json()["episodic"] == {"enabled": False}
    assert client.get("/api/episodes", headers=admin).status_code == 409
    assert client.post("/api/episodes/maintenance", headers=admin).status_code == 409
    assert app.state.service.runtime.tools.get("recall_episodes") is None
    app.state.store.close()


def test_review_cannot_promote_a_missing_source_message(tmp_path: Path):
    app = _app(tmp_path)
    store = app.state.store
    job = store.enqueue_memory_job("deleted-message-id", "原始请求", "原始回复")
    store.claim_memory_job("test-worker")
    review = store.stage_memory_reviews(job["id"], "test-worker", [
        {"content": "没有来源的候选记忆", "kind": "fact", "importance": 3},
    ])[0]
    store.finish_memory_job(job["id"], owner="test-worker")
    client = TestClient(app)
    response = client.post(
        f"/api/memory-reviews/{review['id']}/approve",
        headers={"Authorization": "Bearer admin-secret"},
        json={"content": "没有来源的候选记忆", "kind": "fact", "importance": 3},
    )
    assert response.status_code == 409
    assert store.memory_review(review["id"])["status"] == "pending"
    assert store.memories() == []
    store.close()


def test_review_source_deleted_while_embedding_never_creates_fact(tmp_path: Path):
    app = _app(tmp_path)
    store = app.state.store
    session = store.create_session("待审核")
    source = store.add_message(session["id"], "user", "请记录甲项目")
    job = store.enqueue_memory_job(source["id"], source["content"], "收到")
    store.claim_memory_job("test-worker")
    review = store.stage_memory_reviews(job["id"], "test-worker", [
        {"content": "甲项目已开始", "kind": "fact", "importance": 3},
    ])[0]
    store.finish_memory_job(job["id"], owner="test-worker")
    started = threading.Event()
    release = threading.Event()

    class PausingEmbedder:
        enabled = True
        timeout_seconds = 10

        async def embed(self, _content):
            started.set()
            await asyncio.to_thread(release.wait, 5)
            return []

    app.state.service.runtime.memory.embedder = PausingEmbedder()
    admin = {"Authorization": "Bearer admin-secret"}
    outcome: dict[str, object] = {}
    with TestClient(app) as client:
        def approve_request():
            try:
                outcome["status"] = client.post(
                    f"/api/memory-reviews/{review['id']}/approve", headers=admin,
                    json={"content": "甲项目已开始", "kind": "fact", "importance": 3},
                ).status_code
            except BaseException as exc:
                outcome["error"] = type(exc).__name__

        thread = threading.Thread(target=approve_request, daemon=True)
        thread.start()
        assert started.wait(5)
        assert client.delete(f"/api/sessions/{session['id']}", headers=admin).status_code == 204
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert outcome == {"status": 409}
        assert store.memories() == []


def test_delete_session_cancels_active_turn_before_source_cleanup(tmp_path: Path):
    app = _app(tmp_path)
    runtime = _without_model_retrieval(app)
    started = threading.Event()

    async def slow_chat(_messages, **_kwargs):
        started.set()
        await asyncio.sleep(60)
        return ChatResult(content="不应完成", raw_message={"content": "不应完成"})

    runtime.llm.chat = slow_chat
    session = app.state.store.create_session("正在执行")
    admin = {"Authorization": "Bearer admin-secret"}
    outcome: dict[str, object] = {}
    with TestClient(app) as client:
        def chat_request():
            try:
                outcome["status"] = client.post(
                    f"/api/sessions/{session['id']}/chat", headers=admin,
                    json={"content": "整理长任务"},
                ).status_code
            except BaseException as exc:
                outcome["error"] = type(exc).__name__

        thread = threading.Thread(target=chat_request, daemon=True)
        thread.start()
        assert started.wait(5)
        response = client.delete(f"/api/sessions/{session['id']}", headers=admin)
        assert response.status_code == 204
        thread.join(5)
        assert not thread.is_alive()
        assert app.state.store.session(session["id"]) is None
        assert app.state.service.episodes.list(status="all") == []
        assert "status" in outcome or "error" in outcome


def test_loop_guard_final_model_call_is_budgeted_and_traced(tmp_path: Path):
    app = _app(tmp_path)
    runtime = _without_model_retrieval(app)
    calls = 0

    async def repeated_tool(_messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 3:
            call = {"id": f"calc-{calls}", "name": "calculate", "arguments": {"expression": "1+1"}}
            return ChatResult(tool_calls=[call], raw_message={"tool_calls": [{"id": call["id"]}]}, usage={"total_tokens": 2})
        return ChatResult(content="停止重复调用", raw_message={"content": "停止重复调用"}, usage={"total_tokens": 9})

    runtime.llm.chat = repeated_tool
    session = app.state.store.create_session("loop guard")
    assistant, _, trace = asyncio.run(runtime.run(session["id"], "请计算 1+1", iteration_limit=4, skip_memory_enqueue=True))
    assert assistant["content"] == "停止重复调用"
    assert len(trace["metadata"]["llm_calls"]) == 4
    assert trace["metadata"]["llm_calls"][-1]["usage"]["total_tokens"] == 9
    assert trace["metadata"]["context_budget"]["loop_guard_final_chars"] <= runtime.settings.context_char_budget
    app.state.store.close()


def test_emergency_context_retry_reports_final_budget_stats(tmp_path: Path):
    app = _app(tmp_path)
    runtime = _without_model_retrieval(app)
    calls = 0

    async def context_retry(_messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ContextLengthError("context too long")
        return ChatResult(content="已缩减上下文", raw_message={"content": "已缩减上下文"})

    runtime.llm.chat = context_retry
    session = app.state.store.create_session("emergency")
    _, _, trace = asyncio.run(runtime.run(session["id"], "整理客户需求文档", skip_memory_enqueue=True))
    budget = trace["metadata"]["context_budget"]
    assert calls == 2 and budget["emergency_retry"] is True
    assert budget["final_chars"] == budget["emergency"]["final_chars"]
    assert budget["truncated_messages"] == budget["normal"]["truncated_messages"] + budget["emergency"]["truncated_messages"]
    assert budget["preserved_context_frame"] == budget["emergency"]["preserved_context_frame"]
    app.state.store.close()


def test_skill_revision_traces_injection_and_only_successful_load(tmp_path: Path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 整理任务的技能\ntriggers: 整理任务\n---\n\n# Demo\n按步骤整理。\n",
        encoding="utf-8",
    )
    app = _app(tmp_path, skills_root=skills_root)
    runtime = _without_model_retrieval(app)
    calls = 0

    async def fake_chat(_messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            tool = {"id": "load", "name": "load_skill", "arguments": {"name": "demo"}}
            return ChatResult(tool_calls=[tool], raw_message={"tool_calls": [{"id": "load"}]})
        return ChatResult(content="整理完毕", raw_message={"content": "整理完毕"})

    runtime.llm.chat = fake_chat
    session = app.state.store.create_session("技能记录")
    _, _, trace = asyncio.run(runtime.run(session["id"], "请整理任务", skip_memory_enqueue=True))
    revision = app.state.service.skills.get("demo").revision
    assert trace["metadata"]["skills"]["matched"][0]["revision"] == revision
    assert trace["metadata"]["skills"]["loaded"] == [{"name": "demo", "revision": revision}]

    async def missing_chat(_messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            tool = {"id": "missing", "name": "load_skill", "arguments": {"name": "missing"}}
            return ChatResult(tool_calls=[tool], raw_message={"tool_calls": [{"id": "missing"}]})
        return ChatResult(content="已答复", raw_message={"content": "已答复"})

    runtime.llm.chat = missing_chat
    other = app.state.store.create_session("失败加载")
    _, _, other_trace = asyncio.run(runtime.run(other["id"], "请整理任务", skip_memory_enqueue=True))
    assert other_trace["metadata"]["skills"]["loaded"] == []
    app.state.store.close()


def test_skill_trace_only_counts_titles_surviving_prompt_budget(tmp_path: Path):
    skills_root = tmp_path / "skills"
    for name in ("alpha", "beta"):
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: 整理任务\ntriggers: 整理任务\n---\n\n# {name}\n" + "按步骤完成。" * 40,
            encoding="utf-8",
        )
    app = _app(tmp_path, skills_root=skills_root, procedural_chars=100)
    runtime = _without_model_retrieval(app)

    async def fake_chat(_messages, **_kwargs):
        return ChatResult(content="完成", raw_message={"content": "完成"})

    runtime.llm.chat = fake_chat
    session = app.state.store.create_session("预算裁剪")
    _, _, trace = asyncio.run(runtime.run(session["id"], "整理任务", skip_memory_enqueue=True))
    skills = trace["metadata"]["skills"]
    assert len(skills["matched"]) == 2
    assert [item["name"] for item in skills["injected"]] == ["alpha"]
    assert skills["loaded"] == []
    app.state.store.close()
