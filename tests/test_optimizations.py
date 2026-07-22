import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from memoria.api import create_app
from eval.memory_eval import evaluate_rankings
from memoria.memory import MemoryEngine, MemoryJobWorker, MemoryQueryPlanner
from memoria.observability import TurnTracer
from memoria.store import Store
from memoria.tools.registry import Tool, ToolRegistry


def test_memory_jobs_are_idempotent_and_undo_restores_previous(tmp_path: Path):
    store = Store(tmp_path / "jobs.db")
    old = store.add_memory("用户喜欢咖啡", "preference", 3, source_ref="old-message")
    new = store.add_memory("用户改为喜欢茶", "preference", 4, supersedes_id=old["id"], source_ref="new-message")
    first = store.enqueue_memory_job("message-1", "u", "a")
    second = store.enqueue_memory_job("message-1", "u", "a")
    assert first["id"] == second["id"]
    assert store.claim_memory_job()["id"] == first["id"]
    store.finish_memory_job(first["id"])
    assert store.memory_jobs()[0]["status"] == "completed"
    preview = store.undo_memory_sources(["new-message"], dry_run=True)
    assert new["id"] in preview["affected_ids"] and old["id"] in preview["restored_ids"]
    store.undo_memory_sources(["new-message"])
    assert store.memory(old["id"])["status"] == "active"
    assert store.memory(new["id"])["status"] == "superseded"
    reinforced = asyncio.run(MemoryEngine(store).remember("用户喜欢咖啡", "preference", 3, "conversation", "reinforce-message"))
    assert reinforced.action == "reinforced" and store.memory(old["id"])["reinforcement"] == 2
    assert old["id"] in store.undo_memory_sources(["reinforce-message"])["affected_ids"]
    assert store.memory(old["id"])["reinforcement"] == 1
    first = asyncio.run(MemoryEngine(store).remember("用户喜欢咖啡", "preference", 3, "conversation", "idempotent-message"))
    second = asyncio.run(MemoryEngine(store).remember("用户喜欢咖啡", "preference", 3, "conversation", "idempotent-message"))
    assert first.action == "reinforced" and second.action == "skipped"
    assert store.memory(old["id"])["reinforcement"] == 2


def test_fts_and_session_delete_cleanup(tmp_path: Path):
    store = Store(tmp_path / "fts.db")
    session = store.create_session()
    store.add_message(session["id"], "user", "独特检索词三体问题")
    assert store.search_messages("独特检索词")
    assert store.delete_session(session["id"])
    assert store.search_messages("独特检索词") == []


def test_retrieval_planner_gates_greeting_and_applies_kinds():
    planner = MemoryQueryPlanner()
    greeting = asyncio.run(planner.plan("你好", []))
    regular = asyncio.run(planner.plan("我的学习目标是什么", []))
    assert not greeting.needed
    assert regular.needed


def test_tool_permissions_hooks_validation_and_output_cap():
    registry = ToolRegistry()
    touched = []
    async def write(_):
        touched.append(True)
        return {"data": "x" * 100}
    async def hook(tool, arguments):
        assert tool.name == "write_test" and "value" in arguments
    registry.register_hook(hook)
    registry.register(Tool("write_test", "test", {"type":"object","properties":{"value":{"type":"integer"}},"required":["value"],"additionalProperties":False}, write, "write", max_output_chars=20))
    denied = asyncio.run(registry.execute("write_test", {"value": 1}))
    unknown = asyncio.run(registry.execute("write_test", {"value": 1, "extra": True}, allow_write=True))
    allowed = asyncio.run(registry.execute("write_test", {"value": 1}, allow_write=True))
    assert not denied.ok and not unknown.ok and not touched[:0]
    assert allowed.ok and "输出已截断" in allowed.content and touched == [True]


def test_trace_metadata_and_eval_metrics(tmp_path: Path):
    store = Store(tmp_path / "trace.db")
    trace = TurnTracer(store, "s").finish("completed", 1, [], [], metadata={"llm_calls":[{"duration_ms":12,"usage":{"total_tokens":8}}]})
    assert trace["metadata"]["llm_calls"][0]["usage"]["total_tokens"] == 8
    report = evaluate_rankings(
        [{"id":"a","expected_ids":["m1"]},{"id":"b","expected_ids":[]}],
        {"a":["m2","m1"],"b":[]}, k=2,
    )
    assert report.recall_at_k == 1 and report.mean_reciprocal_rank == 0.5 and report.wrong_injection_rate == 0


def test_memory_worker_processes_durable_job(tmp_path: Path):
    class FakeLLM:
        async def extract_memories(self, user_text, assistant_text):
            return [{"content":"用户偏好深色主题","kind":"preference","importance":4}]
    store = Store(tmp_path / "worker.db")
    store.enqueue_memory_job("message-worker", "我喜欢深色主题", "知道了")
    worker = MemoryJobWorker(store, FakeLLM(), MemoryEngine(store))
    assert asyncio.run(worker.process_once())
    assert store.memory_jobs()[0]["status"] == "completed"
    assert store.memories()[0]["source_ref"] == "message-worker"


def test_sse_chat_endpoint_emits_delta_and_complete(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(f'''[llm.main]\nmodel="test"\napi_key="x"\nbase_url="http://example.test/v1"\n[storage]\ndatabase="{tmp_path / 'stream.db'}"\n''', encoding="utf-8")
    app = create_app(config)

    async def fake_chat(session_id, content, on_event=None):
        if on_event:
            await on_event({"type":"delta","content":"流式"})
        message = app.state.store.add_message(session_id, "assistant", "流式完成")
        trace = app.state.store.add_trace(session_id, "completed", 1, 3, [], [], metadata={})
        return message, [], trace

    app.state.service.chat_with_trace = fake_chat
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"title":"stream"}).json()
        response = client.post(f"/api/sessions/{session['id']}/chat/stream", json={"content":"hello"})
        assert response.status_code == 200
        assert '"type": "delta"' in response.text
        assert '"type": "complete"' in response.text
