from pathlib import Path

from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.lifecycle import Phase, Pipeline, TurnContext
from memoria.memory import MemoryEngine
from memoria.store import Store
from memoria.tools import build_registry
from memoria.tools.loop_guard import ToolLoopGuard
from memoria.prompting import ContextBudget
from memoria.llm import LLMClient, ContextLengthError, ContentSafetyError, ProviderError
import asyncio


def test_session_and_memory_crud(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]\nmodel="test"\napi_key="x"\nbase_url="http://example.test/v1"\n'''
        f'''[storage]\ndatabase="{tmp_path / 'test.db'}"\n''',
        encoding="utf-8",
    )
    client = TestClient(create_app(config))
    session = client.post("/api/sessions", json={"title": "测试"}).json()
    assert client.get("/api/sessions").json()[0]["id"] == session["id"]
    memory = client.post("/api/memories", json={"content": "喜欢简洁回答", "kind": "preference", "importance": 4}).json()
    assert client.get("/api/memories?q=简洁").json()[0]["id"] == memory["id"]
    assert client.patch(f"/api/memories/{memory['id']}", json={"importance": 5}).json()["importance"] == 5
    assert client.delete(f"/api/memories/{memory['id']}").status_code == 204
    assert client.patch(f"/api/sessions/{session['id']}", json={"title": "已重命名"}).json()["title"] == "已重命名"
    invalid = client.post("/api/memories", json={"content": "x", "unknown": True})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    assert invalid.headers["X-Request-ID"]


def test_openapi_and_tool_debug(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(f'''[llm.main]\nmodel="test"\napi_key="x"\nbase_url="http://example.test/v1"\n[storage]\ndatabase="{tmp_path / 'api.db'}"\n''', encoding="utf-8")
    client = TestClient(create_app(config))
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "0.2.0"
    assert "/api/tools/{tool_name}/execute" in schema["paths"]
    result = client.post("/api/tools/calculate/execute", json={"arguments": {"expression": "6*7"}})
    assert result.status_code == 200 and result.json()["content"] == "42"
    blocked = client.post("/api/tools/memorize/execute", json={"arguments": {"content": "test"}})
    assert blocked.status_code == 409


def test_memory_dedup_and_rank(tmp_path: Path):
    store = Store(tmp_path / "memory.db")
    engine = MemoryEngine(store)
    assert engine.add_if_new("用户喜欢简洁回答", "preference", 4, "test")
    assert engine.add_if_new("用户喜欢简洁回答", "preference", 4, "test") is None
    assert engine.retrieve("简洁回答")[0]["kind"] == "preference"


def test_builtin_tools_and_trace(tmp_path: Path):
    store = Store(tmp_path / "tools.db")
    registry = build_registry(store)
    result = asyncio.run(registry.execute("calculate", {"expression": "(2 + 3) * 4"}))
    assert result.ok and result.content == "20"
    rejected = asyncio.run(registry.execute("calculate", {"expression": "__import__('os')"}))
    assert not rejected.ok
    trace = store.add_trace("s1", "completed", 2, 12, [], [{"name": "calculate"}])
    assert trace["tools"][0]["name"] == "calculate"


def test_lifecycle_priority():
    pipeline, order = Pipeline(), []
    async def later(context): order.append("later")
    async def first(context): order.append("first")
    pipeline.register(Phase.BEFORE_TURN, later, 100)
    pipeline.register(Phase.BEFORE_TURN, first, 10)
    asyncio.run(pipeline.run(Phase.BEFORE_TURN, TurnContext("s", "hello")))
    assert order == ["first", "later"]


def test_context_budget_keeps_recent_and_valid_tool_protocol():
    huge = "x" * 5000
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old " + huge},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": huge},
        {"role": "user", "content": "latest question"},
    ]
    result = ContextBudget(max_chars=4000, tool_result_chars=1000).apply(messages)
    assert result.final_chars <= 4000
    assert result.messages[0]["role"] == "system"
    assert result.messages[-1]["content"] == "latest question"
    call_ids = {c["id"] for m in result.messages for c in (m.get("tool_calls") or [])}
    assert all(m.get("role") != "tool" or m.get("tool_call_id") in call_ids for m in result.messages)


def test_tool_loop_guard_uses_stable_argument_signature():
    guard = ToolLoopGuard(max_identical=2)
    first = [{"name": "calculate", "arguments": {"b": 2, "a": 1}}]
    same = [{"name": "calculate", "arguments": {"a": 1, "b": 2}}]
    assert guard.check(first)[0]
    assert guard.check(same)[0]
    assert not guard.check(first)[0]


def test_provider_error_classification():
    import httpx
    try:
        LLMClient._raise_response_error(httpx.Response(400, text="context_length_exceeded"))
        assert False
    except ContextLengthError:
        pass
    try:
        LLMClient._raise_response_error(httpx.Response(400, text="content_filter"))
        assert False
    except ContentSafetyError:
        pass
    assert LLMClient._retryable(ProviderError("HTTP 503 temporarily unavailable"))
    assert not LLMClient._retryable(ProviderError("HTTP 401 invalid key"))


def test_trace_redacts_credentials(tmp_path: Path):
    store = Store(tmp_path / "trace.db")
    from memoria.observability import TurnTracer
    trace = TurnTracer(store, "s").finish("failed", 1, [{"id":"m1","content":"private","kind":"fact"}], [{"name":"x","arguments":{"api_key":"sk-1234567890"},"preview":"Bearer abcdef123"}], "sk-abcdefghijk")
    dumped = str(trace)
    assert "private" not in dumped and "1234567890" not in dumped and "abcdef123" not in dumped
