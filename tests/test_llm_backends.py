"""Backend routing + local OpenAI-compatible server wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from memoria.config import ModelConfig, Settings
from memoria.llm import LLMClient, classify_openai_server, detect_backend_kind, resolve_backend
from memoria.llm.backends.openai_http import OpenAICompatBackend
from memoria.llm.backends.process import ProcessLocalBackend
from memoria.llm.server import create_local_openai_app


def test_detect_backend_kind():
    assert detect_backend_kind(ModelConfig("m", "", "local://cuda")) == "process_local"
    assert detect_backend_kind(ModelConfig("m", "k", "https://api.deepseek.com/v1")) == "openai_http"
    assert detect_backend_kind(ModelConfig("m", "", "http://127.0.0.1:8080/v1")) == "openai_http"


def test_classify_openai_server_labels():
    assert classify_openai_server("https://api.deepseek.com/v1") == "deepseek"
    assert classify_openai_server("http://127.0.0.1:11434/v1") == "ollama"
    assert classify_openai_server("http://127.0.0.1:8080/v1") == "openai-local"


def test_resolve_backend_types():
    assert isinstance(
        resolve_backend(ModelConfig("m", "", "local://cpu"), timeout_seconds=30, max_retries=0),
        ProcessLocalBackend,
    )
    assert isinstance(
        resolve_backend(ModelConfig("m", "k", "https://example.test/v1"), timeout_seconds=30, max_retries=0),
        OpenAICompatBackend,
    )


def test_llm_client_uses_openai_http_for_loopback(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="model/Qwen3.5-2B"
api_key=""
base_url="http://127.0.0.1:8080/v1"
[storage]
database="{(tmp_path / "b.db").as_posix()}"
''',
        encoding="utf-8",
    )
    settings = Settings.load(config)
    client = LLMClient(settings)
    assert client.backend_label() == "openai-local"
    assert isinstance(client.backend_for(), OpenAICompatBackend)


def test_local_openai_server_health_and_chat_with_stub(monkeypatch):
    async def fake_chat(self, messages, tools, max_tokens, timeout):
        return {
            "content": "pong",
            "tool_calls": [],
            "raw_message": {"role": "assistant", "content": "pong", "tool_calls": []},
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "finish_reason": "stop",
            "device": "cpu",
        }

    monkeypatch.setattr("memoria.llm.backends.process_engine.LocalCausalLM.chat", fake_chat)
    app = create_local_openai_app(model="model/Qwen3.5-2B", device="cpu", served_model_name="Qwen3.5-2B")
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        models = client.get("/v1/models").json()
        assert models["data"][0]["id"] == "Qwen3.5-2B"
        response = client.post(
            "/v1/chat/completions",
            json={"model": "Qwen3.5-2B", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["choices"][0]["message"]["content"] == "pong"


def test_llm_client_http_against_mock_openai_endpoint(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hello-http"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
        )

    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="Qwen3.5-2B"
api_key=""
base_url="http://127.0.0.1:8080/v1"
[storage]
database="{(tmp_path / "http.db").as_posix()}"
''',
        encoding="utf-8",
    )
    settings = Settings.load(config)
    result = asyncio.run(
        LLMClient(settings, transport=httpx.MockTransport(handler)).chat(
            [{"role": "user", "content": "ping"}],
            max_tokens=8,
        )
    )
    assert result.content == "hello-http"