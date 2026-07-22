from pathlib import Path
import sqlite3

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
    created = client.post("/api/memories", json={"content": "喜欢简洁回答", "kind": "preference", "importance": 4}).json()
    assert created["action"] == "created"
    memory = created["memory"]
    assert client.get("/api/memories?q=简洁").json()[0]["id"] == memory["id"]
    reinforced = client.post("/api/memories", json={"content": "喜欢简洁回答", "kind": "preference", "importance": 4})
    assert reinforced.status_code == 200 and reinforced.json()["action"] == "reinforced"
    assert client.get("/api/memories").json()[0]["reinforcement"] == 2
    assert client.get(f"/api/memories/{memory['id']}/history").json() == []
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
    assert schema["info"]["version"] == "0.5.0"
    assert "/api/tools/{tool_name}/execute" in schema["paths"]
    assert "/api/memories/reindex" in schema["paths"]
    assert "/api/memories/{memory_id}/history" in schema["paths"]
    assert "/api/sessions/{session_id}/chat/stream" in schema["paths"]
    assert "/api/sessions/{session_id}/cancel" in schema["paths"]
    assert "/api/memories/undo" in schema["paths"]
    assert "/api/memory-jobs" in schema["paths"]
    assert client.post("/api/memories/reindex").json() == {"enabled": False, "indexed": 0, "remaining": 0}
    result = client.post("/api/tools/calculate/execute", json={"arguments": {"expression": "6*7"}})
    assert result.status_code == 200 and result.json()["content"] == "42"
    blocked = client.post("/api/tools/memorize/execute", json={"arguments": {"content": "test"}})
    assert blocked.status_code == 409


def test_memory_dedup_and_rank(tmp_path: Path):
    store = Store(tmp_path / "memory.db")
    engine = MemoryEngine(store)
    assert asyncio.run(engine.add_if_new("用户喜欢简洁回答", "preference", 4, "test"))
    assert asyncio.run(engine.add_if_new("用户喜欢简洁回答", "preference", 4, "test")) is None
    assert store.memories()[0]["reinforcement"] == 2
    assert asyncio.run(engine.retrieve("简洁回答"))[0]["kind"] == "preference"


class FakeEmbedder:
    enabled = True
    timeout_seconds = 1

    @staticmethod
    def vector(text: str) -> list[float]:
        if any(word in text for word in ("爬山", "户外运动", "徒步")):
            return [1.0, 0.0]
        return [0.0, 1.0]

    async def embed(self, text: str) -> list[float]:
        return self.vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]


def test_semantic_retrieval_and_lazy_embedding_backfill(tmp_path: Path):
    store = Store(tmp_path / "semantic.db")
    hiking = store.add_memory("用户周末常去爬山", "preference", 4, "test")
    store.add_memory("用户偏好喝红茶", "preference", 3, "test")
    engine = MemoryEngine(store, FakeEmbedder())

    result = asyncio.run(engine.retrieve("休息日偏爱的户外运动", 1))

    assert result[0]["id"] == hiking["id"]
    assert result[0]["retrieval"]["vector_rank"] == 1
    assert store.memories(limit=10)[0].get("embedding") is not None
    assert store.update_memory(hiking["id"], {"content": "用户偶尔去爬山"})["embedding"] is None


def test_reindex_reports_disabled_embedding(tmp_path: Path):
    store = Store(tmp_path / "disabled.db")
    store.add_memory("一条旧记忆")
    result = asyncio.run(MemoryEngine(store).reindex())
    assert result == {"enabled": False, "indexed": 0, "remaining": 1}


def test_memory_supersede_keeps_history_and_hides_old_item(tmp_path: Path):
    async def supersede_new_preference(content, kind, candidates):
        return {"action": "supersede", "target_id": candidates[0]["id"], "reason": "用户明确改变偏好"}

    store = Store(tmp_path / "supersede.db")
    engine = MemoryEngine(store, FakeEmbedder(), supersede_new_preference)
    old = asyncio.run(engine.add_if_new("用户喜欢喝咖啡", "preference", 3, "test"))
    new = asyncio.run(engine.add_if_new("用户现在改为喜欢喝红茶", "preference", 4, "test"))

    assert old and new and new["supersedes_id"] == old["id"]
    assert store.memory(old["id"])["status"] == "superseded"
    assert [item["id"] for item in store.memories()] == [new["id"]]
    assert store.memories(status="superseded")[0]["id"] == old["id"]
    history = store.memory_history(new["id"])
    assert history[0]["old_memory_id"] == old["id"]
    assert history[0]["reason"] == "用户明确改变偏好"


def test_store_migrates_legacy_memory_schema(tmp_path: Path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE memories (
        id TEXT PRIMARY KEY, content TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact',
        importance INTEGER NOT NULL DEFAULT 3, source TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    db.execute("INSERT INTO memories VALUES ('legacy','旧记忆','fact',3,'test','2026-01-01','2026-01-01')")
    db.commit()
    db.close()

    store = Store(path)
    item = store.memory("legacy")
    assert item and item["status"] == "active" and item["reinforcement"] == 1
    assert item["embedding"] is None and item["supersedes_id"] is None


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
