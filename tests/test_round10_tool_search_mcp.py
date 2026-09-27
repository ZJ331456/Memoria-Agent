import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.mcp.client import McpClient
from memoria.mcp.host import local_tool_name
from memoria.tools.builtin import build_registry
from memoria.tools.search import ToolPresentation
from memoria.store import Store


def test_local_tool_name():
    assert local_tool_name("demo", "echo") == "mcp_demo_echo"
    assert local_tool_name("my-server", "list.files") == "mcp_my_server_list_files"


def test_tool_search_presentation(tmp_path: Path):
    store = Store(tmp_path / "search.db")
    registry = build_registry(store)
    presentation = ToolPresentation(registry, enabled=True)
    names = {schema["function"]["name"] for schema in presentation.schemas()}
    assert "tool_search" in names
    assert "tool_call" in names
    assert "calculate" in names
    assert "memorize" not in names
    assert "http_get" not in names
    found = presentation.search("记忆")
    assert found["matched_groups"]
    decoded = presentation.decode("tool_call", {"name": "memorize", "arguments": {"content": "hello"}})
    assert decoded == ("memorize", {"content": "hello"})
    blocked = presentation.decode("memorize", {"content": "hello"})
    assert isinstance(blocked, str) and "tool_search" in blocked
    catalog = presentation.catalog_prompt()
    assert "可搜索工具目录" in catalog and "memorize" in catalog


def test_tool_search_disabled_exposes_all(tmp_path: Path):
    store = Store(tmp_path / "nosearch.db")
    registry = build_registry(store)
    presentation = ToolPresentation(registry, enabled=False)
    names = {schema["function"]["name"] for schema in presentation.schemas()}
    assert "memorize" in names
    assert "tool_call" not in names
    assert "tool_search" not in names


def test_mcp_demo_client_roundtrip():
    async def run():
        client = McpClient("demo", ("python", "-m", "memoria.mcp.demo_server"))
        tools = await client.connect()
        assert {item.name for item in tools} == {"echo", "add"}
        assert await client.call("echo", {"text": "ping"}) == "ping"
        assert await client.call("add", {"a": 2, "b": 3}) == "5.0"
        await client.disconnect()

    asyncio.run(run())


def test_api_tool_search_and_mcp(tmp_path: Path):
    mcp_config = tmp_path / "mcp_servers.json"
    mcp_config.write_text(
        """
{
  "servers": [
    {
      "name": "demo",
      "command": ["python", "-m", "memoria.mcp.demo_server"],
      "enabled": true,
      "risk": "read-only",
      "timeout_seconds": 15
    }
  ]
}
""",
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="test"
api_key="x"
base_url="http://example.test/v1"
[agent.tools]
search_enabled=true
[agent.mcp]
enabled=true
config_file="{mcp_config}"
[storage]
database="{tmp_path / 'round10.db'}"
''',
        encoding="utf-8",
    )
    app = create_app(config)
    with TestClient(app) as client:
        overview = client.get("/api/overview").json()
        assert overview["tool_search"]["enabled"] is True
        assert "tool_search" in {tool["name"] for tool in overview["tools"]}
        assert overview["mcp"]["tool_count"] >= 2
        assert any(tool["name"] == "mcp_demo_echo" for tool in overview["tools"])
        search = client.post("/api/tools/search", json={"query": "mcp echo"}).json()
        assert search["matched_groups"]
        echo = client.post(
            "/api/tools/mcp_demo_echo/execute",
            json={"arguments": {"text": "hello-mcp"}, "confirm_write": False},
        ).json()
        assert echo["ok"] is True
        assert echo["content"] == "hello-mcp"
        status = client.get("/api/mcp").json()
        assert status["servers"][0]["connected"] is True
        reloaded = client.post("/api/mcp/reload").json()
        assert reloaded["status"]["tool_count"] >= 2
