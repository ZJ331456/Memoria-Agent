import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.memory import export_memories_markdown
from memoria.observability import EventBus, RequestContext, current_session_id, snapshot
from memoria.prompting import PromptAssembler
from memoria.prompting.assembler import SYSTEM_CONTEXT_FRAME_MARKER as FRAME_MARKER
from memoria.runtime.agent import AgentRuntime
from memoria.runtime.compaction import SessionCompactor
from memoria.store import Store
from memoria.tools.registry import Tool, ToolRegistry


def test_prompt_assembler_builds_system_context_frame():
    assembled = PromptAssembler().assemble(
        "你是小琴宝",
        memories=[{"id": "m1", "kind": "preference", "content": "喜欢简洁回答"}],
        compaction_summary="## Goal\n继续学习 Rust",
        interrupt_note="上一轮被中断",
    )
    messages = assembled.as_messages()
    assert messages[0] == {"role": "system", "content": "你是小琴宝"}
    assert messages[1]["role"] == "user"
    assert FRAME_MARKER in messages[1]["content"]
    assert "Long-term Memory" in messages[1]["content"]
    assert "Session Summary" in messages[1]["content"]
    assert "Interrupt" in messages[1]["content"]
    assert "喜欢简洁回答" in messages[1]["content"]


def test_session_compaction_fallback_and_reuse(tmp_path: Path):
    store = Store(tmp_path / "compact.db")
    session = store.create_session("compact")
    for index in range(12):
        store.add_message(session["id"], "user" if index % 2 == 0 else "assistant", f"消息{index} 关于 Rust")
    first = asyncio.run(SessionCompactor(store, summarizer=None, min_messages=4).ensure(session["id"], window=4))
    assert first and "## Goal" in first.summary and not first.reused
    second = asyncio.run(SessionCompactor(store, summarizer=None, min_messages=4).ensure(session["id"], window=4))
    assert second and second.reused and second.summary == first.summary
    saved = store.session_compaction(session["id"])
    assert saved and saved["summary"] == first.summary


def test_event_bus_and_request_context():
    bus = EventBus()
    seen = []

    async def listener(event, payload):
        seen.append((event, payload["value"], current_session_id.get()))

    bus.subscribe(listener)
    with RequestContext(session_id="s1", turn_id="t1", request_id="r1"):
        assert snapshot()["session_id"] == "s1"
        asyncio.run(bus.emit("demo", {"value": 7}))
    assert seen == [("demo", 7, "s1")]
    assert current_session_id.get() is None


def test_parallel_tool_execution_records_flag():
    registry = ToolRegistry()
    order = []

    async def slow(arguments):
        order.append(arguments["name"])
        await asyncio.sleep(0.05)
        return {"name": arguments["name"]}

    registry.register(
        Tool(
            "echo",
            "echo",
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
            slow,
        )
    )

    async def run():
        return await asyncio.gather(
            registry.execute("echo", {"name": "a"}),
            registry.execute("echo", {"name": "b"}),
        )

    results = asyncio.run(run())
    assert all(item.ok for item in results)
    assert set(order) == {"a", "b"}


def test_interrupt_note_persists_and_clears(tmp_path: Path):
    store = Store(tmp_path / "interrupt.db")
    session = store.create_session()
    store.set_interrupt_note(session["id"], "上一轮被中断")
    assert store.session(session["id"])["interrupt_note"] == "上一轮被中断"
    store.clear_interrupt_note(session["id"])
    assert store.session(session["id"])["interrupt_note"] is None


def test_markdown_export_and_api(tmp_path: Path):
    store = Store(tmp_path / "md.db")
    store.add_memory("喜欢红茶", "preference", 4)
    store.add_memory("学习目标是掌握 Rust", "goal", 5)
    text = export_memories_markdown(store.memories())
    assert "# MEMORY" in text and "## Preferences" in text and "喜欢红茶" in text

    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="test"
api_key="x"
base_url="http://example.test/v1"
[storage]
database="{tmp_path / 'api-md.db'}"
''',
        encoding="utf-8",
    )
    app = create_app(config)
    app.state.store.add_memory("导出测试记忆", "fact", 3)
    with TestClient(app) as client:
        response = client.get("/api/memories/export.md")
        assert response.status_code == 200
        assert "导出测试记忆" in response.text
        assert "text/markdown" in response.headers["content-type"]


def test_iteration_limit_zero_means_unlimited_with_hard_cap():
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.settings = type("S", (), {"max_iterations": 0})()
    assert runtime._iteration_limit() is None
    runtime.settings = type("S", (), {"max_iterations": 40})()
    assert runtime._iteration_limit() == 40
    runtime.settings = type("S", (), {"max_iterations": 999})()
    assert runtime._iteration_limit() == AgentRuntime.HARD_ITERATION_CAP
