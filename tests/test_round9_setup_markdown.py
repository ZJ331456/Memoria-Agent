import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from memoria.memory.layer import MarkdownMemoryLayer
from memoria.models_config import apply_overrides, load_model_overrides, save_model_overrides
from memoria.api import create_app
from memoria.config import ModelConfig, Settings
from memoria.store import Store


def test_markdown_layer_sync_pending_and_self(tmp_path: Path):
    layer = MarkdownMemoryLayer(tmp_path / "markdown")
    store = Store(tmp_path / "md.db")
    memory = store.add_memory("喜欢简洁回答", "preference", 4)
    content = layer.sync_memory(store.memories())
    assert "喜欢简洁回答" in content and memory["id"] in content
    layer.append_pending(source_ref="src-1", content="喜欢简洁回答", kind="preference")
    assert "- [open]" in layer.read("PENDING")
    layer.complete_pending(source_ref="src-1", content="喜欢简洁回答", kind="preference")
    pending = layer.read("PENDING")
    assert "- [open]" not in pending or "喜欢简洁回答" not in pending.split("## Open")[1].split("## Done")[0]
    assert "- [done]" in pending
    layer.write("SELF", "# SELF\n\n我是测试助手\n")
    assert "测试助手" in layer.self_excerpt()


def test_model_override_roundtrip(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="old"
api_key="old-key"
base_url="http://old.test/v1"
[storage]
database="{tmp_path / 'override.db'}"
''',
        encoding="utf-8",
    )
    settings = Settings.load(config)
    save_model_overrides(settings, {"main": {"model": "new-model", "base_url": "http://new.test/v1", "api_key": "new-key"}})
    assert settings.main.model == "new-model"
    assert settings.main.api_key == "new-key"
    reloaded = Settings.load(config)
    assert reloaded.main.model == "new-model"
    assert reloaded.main.api_key == "new-key"
    data = load_model_overrides(tmp_path / "models.override.toml")
    assert data["llm"]["main"]["model"] == "new-model"


def test_setup_and_markdown_api(tmp_path: Path):
    markdown_dir = tmp_path / "markdown"
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="test"
api_key="secret-key"
base_url="http://example.test/v1"
[memory.markdown]
enabled=true
directory="{markdown_dir}"
[storage]
database="{tmp_path / 'api.db'}"
''',
        encoding="utf-8",
    )
    app = create_app(config)
    with TestClient(app) as client:
        status = client.get("/api/setup/status").json()
        assert status["setup_needed"] is False
        assert status["main"]["configured"] is True
        assert "secret-key" not in str(status)
        updated = client.put("/api/settings/models", json={"main": {"model": "hot-model"}}).json()
        assert updated["main"]["model"] == "hot-model"
        self_doc = client.get("/api/markdown/SELF").json()
        assert "SELF" in self_doc["content"]
        written = client.put("/api/markdown/SELF", json={"content": "# SELF\n\n热配置助手\n"}).json()
        assert "热配置助手" in written["content"]
        blocked = client.put("/api/markdown/MEMORY", json={"content": "nope"})
        assert blocked.status_code == 409
        client.post("/api/memories", json={"content": "双层记忆测试", "kind": "fact", "importance": 3})
        synced = client.post("/api/markdown/MEMORY/sync").json()
        assert "双层记忆测试" in synced["content"]
        overview = client.get("/api/overview").json()
        assert overview["markdown"]["enabled"] is True
